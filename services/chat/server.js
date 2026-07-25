// Sentinel Mesh - community chat websocket server (in-memory).
// Public location groups load from the claims hotspots.json.
// Private groups are created by users and joined via a share code.
// AWS SEAM: later, replace in-memory stores + broadcast with
// API Gateway websockets + DynamoDB. Client protocol stays the same.

const { WebSocketServer } = require("ws");
const fs = require("fs");
const path = require("path");

const PORT = 8080;

// ---- Load public location groups from the claims pipeline output ----
function loadLocationGroups() {
  const file = path.join(__dirname, "..", "claims", "data", "curated", "hotspots.json");
  try {
    const raw = JSON.parse(fs.readFileSync(file, "utf-8"));
    const list = Array.isArray(raw) ? raw : raw.hotspots || [];
    const seen = new Set();
    const groups = [];
    for (const h of list) {
      const name = h.name;
      if (!name) continue;
      const id = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
      if (seen.has(id)) continue;
      seen.add(id);
      groups.push({ id, name: `${name} Community`, metro: h.metro || "South Africa",
                    visibility: "public" });
    }
    if (groups.length) {
      console.log(`Loaded ${groups.length} location groups from hotspots.json`);
      return groups;
    }
  } catch (e) {
    console.log("Could not load hotspots.json, using demo groups. (" + e.message + ")");
  }
  // fallback
  return [
    { id: "rondebosch", name: "Rondebosch Watch", metro: "Cape Town", visibility: "public" },
    { id: "bryanston", name: "Bryanston Residents", metro: "Gauteng", visibility: "public" },
    { id: "seapoint", name: "Sea Point Neighbours", metro: "Cape Town", visibility: "public" },
  ];
}

const groups = loadLocationGroups(); // { id, name, metro, visibility, code? }
const messages = [];                 // { id, groupId, kind, author, role, text, peril?, location?, ts }

const memberships = new Map();       // name -> Set(groupId)
function getMembership(name) {
  if (!memberships.has(name)) memberships.set(name, new Set());
  return memberships.get(name);
}
const roles = new Map();
const VALID_ROLES = ["Resident", "Security", "Staff"];

// Only public groups + the user's own private groups are visible to them.
function visibleGroups(name) {
  const mine = getMembership(name);
  return groups.filter((g) => g.visibility === "public" || mine.has(g.id));
}

const wss = new WebSocketServer({ port: PORT });
console.log(`Chat server listening on ws://localhost:${PORT}`);

function send(ws, obj) { if (ws.readyState === ws.OPEN) ws.send(JSON.stringify(obj)); }
function broadcast(obj) {
  const data = JSON.stringify(obj);
  wss.clients.forEach((c) => { if (c.readyState === c.OPEN) c.send(data); });
}
// Send only to clients who can see this group (for private-group updates)
function sendToMembers(groupId, obj) {
  const data = JSON.stringify(obj);
  wss.clients.forEach((c) => {
    if (c.readyState === c.OPEN && getMembership(c.userName).has(groupId)) c.send(data);
  });
}
function makeCode() {
  return Math.random().toString(36).slice(2, 8).toUpperCase();
}

wss.on("connection", (ws) => {
  console.log("Client connected. Total:", wss.clients.size);
  ws.userName = "You";
  ws.userRole = "Resident";

  ws.on("message", (raw) => {
    let msg;
    try { msg = JSON.parse(raw); } catch { return; }

    if (msg.type === "hello") {
      ws.userName = msg.name || "You";
      ws.userRole = VALID_ROLES.includes(msg.role) ? msg.role : "Resident";
      roles.set(ws.userName, ws.userRole);
      send(ws, { type: "init", groups: visibleGroups(ws.userName),
                 messages, myGroups: [...getMembership(ws.userName)] });
      return;
    }

    if (msg.type === "join") {
      const g = groups.find((x) => x.id === msg.groupId);
      if (!g) return;
      if (g.visibility !== "public") return; // private groups need a code
      getMembership(ws.userName).add(msg.groupId);
      send(ws, { type: "myGroups", myGroups: [...getMembership(ws.userName)] });
      return;
    }

    if (msg.type === "joinByCode") {
      const code = String(msg.code || "").trim().toUpperCase();
      const g = groups.find((x) => x.code === code);
      if (!g) { send(ws, { type: "joinResult", ok: false, message: "No group with that code." }); return; }
      getMembership(ws.userName).add(g.id);
      // give this client the group + membership so it appears
      send(ws, { type: "groupAdded", group: g });
      send(ws, { type: "myGroups", myGroups: [...getMembership(ws.userName)] });
      send(ws, { type: "joinResult", ok: true, message: `Joined ${g.name}.`, groupId: g.id });
      return;
    }

    if (msg.type === "leave") {
      getMembership(ws.userName).delete(msg.groupId);
      send(ws, { type: "myGroups", myGroups: [...getMembership(ws.userName)] });
      return;
    }

    if (msg.type === "createGroup") {
      const name = String(msg.name || "").trim().slice(0, 40);
      if (!name) return;
      const base = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
      let id = base || "group", n = 1;
      while (groups.some((g) => g.id === id)) id = `${base}-${n++}`;
      const code = makeCode();
      const group = { id, name, metro: "Private group", visibility: "private", code };
      groups.push(group);
      getMembership(ws.userName).add(id);
      // Only the creator sees it (private). Send them the group + its share code.
      send(ws, { type: "groupAdded", group });
      send(ws, { type: "myGroups", myGroups: [...getMembership(ws.userName)] });
      send(ws, { type: "createdGroup", group });
      return;
    }

    if (msg.type === "send") {
      const g = groups.find((x) => x.id === msg.groupId);
      if (!g) return;
      // must be a member to post in a private group
      if (g.visibility === "private" && !getMembership(ws.userName).has(g.id)) return;
      const record = {
        id: crypto.randomUUID(),
        groupId: msg.groupId,
        kind: msg.kind === "incident" ? "incident" : "chat",
        author: msg.author || "Anonymous",
        role: ws.userRole || "Resident",
        text: String(msg.text || "").slice(0, 500),
        peril: msg.kind === "incident" ? msg.peril : undefined,
        location: msg.kind === "incident" ? msg.location : undefined,
        ts: Date.now(),
      };
      messages.push(record);
      // Public: everyone. Private: only members.
      if (g.visibility === "public") broadcast({ type: "message", message: record });
      else sendToMembers(g.id, { type: "message", message: record });
    }
  });

  ws.on("close", () => console.log("Client disconnected. Total:", wss.clients.size));
});