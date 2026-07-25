"use strict";

const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const path = require("path");

const {
  WebSocket,
  WebSocketServer
} = require("ws");

const PORT = Number(process.env.PORT || 8080);
const HOST = process.env.HOST || "0.0.0.0";

const DATA_DIR =
  process.env.DATA_DIR ||
  path.join(__dirname, "data");

const STATE_FILE = path.join(
  DATA_DIR,
  "chat-state.json"
);

const HOTSPOTS_FILE =
  process.env.HOTSPOTS_FILE ||
  path.join(
    __dirname,
    "..",
    "claims",
    "data",
    "curated",
    "hotspots.json"
  );

const SYSTEM_KEY = process.env.SENTINEL_SYSTEM_KEY || "";

const MAX_MESSAGE_LENGTH = 500;
const MAX_LOCATION_LENGTH = 120;
const MAX_NAME_LENGTH = 40;
const MAX_MESSAGES = 5000;

const VALID_ROLES = new Set([
  "Resident",
  "Security",
  "Staff"
]);

const VALID_PERILS = new Set([
  "Home Invasion",
  "Vehicle Theft",
  "Suspicious Activity"
]);

function cleanString(value, maxLength) {
  return String(value ?? "")
    .replace(/[\u0000-\u001f\u007f]/g, "")
    .trim()
    .slice(0, maxLength);
}

function createGroupId(name) {
  return String(name || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);
}

function makeCode() {
  return crypto
    .randomBytes(4)
    .toString("hex")
    .slice(0, 6)
    .toUpperCase();
}

function fallbackPublicGroups() {
  return [
    {
      id: "rondebosch",
      name: "Rondebosch Watch",
      metro: "Cape Town",
      visibility: "public"
    },
    {
      id: "bryanston",
      name: "Bryanston Residents",
      metro: "Gauteng",
      visibility: "public"
    },
    {
      id: "seapoint",
      name: "Sea Point Neighbours",
      metro: "Cape Town",
      visibility: "public"
    }
  ];
}

function loadPublicGroups() {
  try {
    const fileContents = fs.readFileSync(
      HOTSPOTS_FILE,
      "utf8"
    );

    const parsed = JSON.parse(fileContents);

    let hotspots = [];

    if (Array.isArray(parsed)) {
      hotspots = parsed;
    } else if (Array.isArray(parsed.hotspots)) {
      hotspots = parsed.hotspots;
    } else if (Array.isArray(parsed.locations)) {
      hotspots = parsed.locations;
    } else if (Array.isArray(parsed.data)) {
      hotspots = parsed.data;
    }

    const groups = [];
    const usedIds = new Set();

    for (const hotspot of hotspots) {
      if (!hotspot || typeof hotspot !== "object") {
        continue;
      }

      const locationName = cleanString(
        hotspot.name ||
          hotspot.location ||
          hotspot.suburb ||
          hotspot.area ||
          hotspot.hotspot ||
          hotspot.neighbourhood ||
          hotspot.neighborhood,
        MAX_NAME_LENGTH
      );

      if (!locationName) {
        continue;
      }

      const baseId =
        createGroupId(locationName) || "location";

      let groupId = baseId;
      let number = 2;

      while (usedIds.has(groupId)) {
        groupId = `${baseId}-${number}`;
        number += 1;
      }

      usedIds.add(groupId);

      const customGroupName = cleanString(
        hotspot.groupName ||
          hotspot.group_name,
        MAX_NAME_LENGTH
      );

      const metro = cleanString(
        hotspot.metro ||
          hotspot.city ||
          hotspot.municipality ||
          hotspot.province ||
          "South Africa",
        80
      );

      groups.push({
        id: groupId,
        name:
          customGroupName ||
          `${locationName} Community`,
        metro: metro || "South Africa",
        visibility: "public"
      });
    }

    if (groups.length > 0) {
      console.log(
        `Loaded ${groups.length} public groups from hotspots.json`
      );

      return groups;
    }

    console.warn(
      "hotspots.json did not contain any valid locations."
    );
  } catch (error) {
    console.warn(
      `Could not load hotspots.json: ${error.message}`
    );

    console.warn(
      `Expected file location: ${HOTSPOTS_FILE}`
    );
  }

  console.warn(
    "Using the three fallback demonstration groups."
  );

  return fallbackPublicGroups();
}

const PUBLIC_GROUPS = loadPublicGroups();

function emptyState() {
  return {
    groups: [...PUBLIC_GROUPS],
    messages: [],
    memberships: {}
  };
}

function loadState() {
  fs.mkdirSync(DATA_DIR, {
    recursive: true
  });

  if (!fs.existsSync(STATE_FILE)) {
    return emptyState();
  }

  try {
    const parsed = JSON.parse(
      fs.readFileSync(STATE_FILE, "utf8")
    );

    const storedGroups = Array.isArray(parsed.groups)
      ? parsed.groups
      : [];

    const privateGroups = storedGroups.filter(
      (group) =>
        group &&
        group.visibility === "private"
    );

    const publicGroupIds = new Set(
      PUBLIC_GROUPS.map((group) => group.id)
    );

    const preservedPrivateGroups =
      privateGroups.filter(
        (group) =>
          !publicGroupIds.has(group.id)
      );

    return {
      groups: [
        ...PUBLIC_GROUPS,
        ...preservedPrivateGroups
      ],
      messages: Array.isArray(parsed.messages)
        ? parsed.messages
        : [],
      memberships:
        parsed.memberships &&
        typeof parsed.memberships === "object"
          ? parsed.memberships
          : {}
    };
  } catch (error) {
    console.error(
      `Could not load saved chat state: ${error.message}`
    );

    return emptyState();
  }
}

let state = loadState();
let saveTimer = null;

function saveStateImmediately() {
  try {
    fs.mkdirSync(DATA_DIR, {
      recursive: true
    });

    const temporaryFile =
      `${STATE_FILE}.tmp`;

    fs.writeFileSync(
      temporaryFile,
      JSON.stringify(state, null, 2),
      "utf8"
    );

    fs.renameSync(
      temporaryFile,
      STATE_FILE
    );
  } catch (error) {
    console.error(
      "Could not save chat state:",
      error
    );
  }
}

function saveStateSoon() {
  clearTimeout(saveTimer);

  saveTimer = setTimeout(() => {
    saveStateImmediately();
  }, 100);
}

function membershipFor(userId) {
  if (!Array.isArray(state.memberships[userId])) {
    state.memberships[userId] = [];
  }

  return new Set(
    state.memberships[userId]
  );
}

function setMembership(userId, membership) {
  state.memberships[userId] = [
    ...membership
  ];

  saveStateSoon();
}

function isMember(userId, groupId) {
  return membershipFor(userId)
    .has(groupId);
}

function visibleGroups(userId) {
  return state.groups
    .filter(
      (group) =>
        group.visibility === "public" ||
        isMember(userId, group.id)
    )
    .map((group) => {
      if (
        group.visibility === "private" &&
        !isMember(userId, group.id)
      ) {
        const {
          code,
          ...safeGroup
        } = group;

        return safeGroup;
      }

      return group;
    });
}

function visibleMessages(userId) {
  const permittedGroupIds = new Set(
    visibleGroups(userId).map(
      (group) => group.id
    )
  );

  return state.messages.filter(
    (message) =>
      permittedGroupIds.has(
        message.groupId
      )
  );
}

function send(ws, payload) {
  if (
    ws.readyState === WebSocket.OPEN
  ) {
    ws.send(
      JSON.stringify(payload)
    );
  }
}

function sendError(
  ws,
  message,
  requestId
) {
  send(ws, {
    type: "error",
    message,
    requestId: requestId || null
  });
}

function broadcastToGroup(
  groupId,
  payload
) {
  const group = state.groups.find(
    (item) => item.id === groupId
  );

  if (!group) {
    return;
  }

  for (const client of wss.clients) {
    if (
      client.readyState !== WebSocket.OPEN ||
      !client.authenticated
    ) {
      continue;
    }

    const canReceive =
      group.visibility === "public" ||
      isMember(
        client.userId,
        groupId
      );

    if (canReceive) {
      send(client, payload);
    }
  }
}

function sendInitialState(ws) {
  send(ws, {
    type: "init",
    groups: visibleGroups(ws.userId),
    messages: visibleMessages(ws.userId),
    myGroups: [
      ...membershipFor(ws.userId)
    ]
  });
}

const clientFile = path.join(
  __dirname,
  "client.html"
);

const server = http.createServer(
  (request, response) => {
    let requestUrl;

    try {
      requestUrl = new URL(
        request.url,
        `http://${request.headers.host || "localhost"}`
      );
    } catch {
      response.writeHead(400, {
        "Content-Type":
          "text/plain; charset=utf-8"
      });

      response.end("Invalid request.");
      return;
    }

    if (
      request.method === "GET" &&
      requestUrl.pathname === "/health"
    ) {
      response.writeHead(200, {
        "Content-Type":
          "application/json; charset=utf-8",
        "Cache-Control": "no-store"
      });

      response.end(
        JSON.stringify({
          status: "ok",
          publicGroups:
            PUBLIC_GROUPS.length,
          connectedClients:
            wss ? wss.clients.size : 0
        })
      );

      return;
    }

    if (
      request.method === "POST" &&
      requestUrl.pathname === "/system/announce"
    ) {
      if (
        !SYSTEM_KEY ||
        request.headers["x-sentinel-key"] !== SYSTEM_KEY
      ) {
        response.writeHead(401, {
          "Content-Type":
            "application/json; charset=utf-8"
        });

        response.end(
          JSON.stringify({
            error: "Unauthorised."
          })
        );

        return;
      }

      let body = "";

      request.on("data", (chunk) => {
        body += chunk;

        if (body.length > 8192) {
          request.destroy();
        }
      });

      request.on("end", () => {
        let payload;

        try {
          payload = JSON.parse(body);
        } catch {
          response.writeHead(400, {
            "Content-Type":
              "application/json; charset=utf-8"
          });

          response.end(
            JSON.stringify({
              error: "Invalid JSON."
            })
          );

          return;
        }

        const group = state.groups.find(
          (item) =>
            item.id === payload.groupId
        );

        if (!group) {
          response.writeHead(404, {
            "Content-Type":
              "application/json; charset=utf-8"
          });

          response.end(
            JSON.stringify({
              error: `Unknown group ${payload.groupId}`
            })
          );

          return;
        }

        const text = cleanString(
          payload.text,
          MAX_MESSAGE_LENGTH
        );

        if (!text) {
          response.writeHead(400, {
            "Content-Type":
              "application/json; charset=utf-8"
          });

          response.end(
            JSON.stringify({
              error: "Message text is required."
            })
          );

          return;
        }

        const record = {
          id: crypto.randomUUID(),
          groupId: group.id,
          kind: "incident",
          authorId: "system",
          author: "Sentinel Mesh",
          role: "Staff",
          text,
          peril: VALID_PERILS.has(payload.peril)
            ? payload.peril
            : "Suspicious Activity",
          location: cleanString(
            payload.location,
            MAX_LOCATION_LENGTH
          ),
          ts: Date.now()
        };

        state.messages.push(record);

        if (
          state.messages.length > MAX_MESSAGES
        ) {
          state.messages.splice(
            0,
            state.messages.length - MAX_MESSAGES
          );
        }

        saveStateSoon();

        broadcastToGroup(group.id, {
          type: "message",
          message: record
        });

        response.writeHead(200, {
          "Content-Type":
            "application/json; charset=utf-8"
        });

        response.end(
          JSON.stringify({
            ok: true,
            messageId: record.id,
            groupId: group.id
          })
        );
      });

      return;
    }

    if (
      request.method === "GET" &&
      (
        requestUrl.pathname === "/" ||
        requestUrl.pathname ===
          "/client.html"
      )
    ) {
      fs.readFile(
        clientFile,
        (error, contents) => {
          if (error) {
            console.error(
              "Could not read client.html:",
              error
            );

            response.writeHead(500, {
              "Content-Type":
                "text/plain; charset=utf-8"
            });

            response.end(
              "Could not load the chat client."
            );

            return;
          }

          response.writeHead(200, {
            "Content-Type":
              "text/html; charset=utf-8",
            "Cache-Control": "no-cache",
            "X-Content-Type-Options":
              "nosniff",
            "Referrer-Policy":
              "same-origin",
            "Content-Security-Policy":
              "default-src 'self'; " +
              "style-src 'self' 'unsafe-inline'; " +
              "script-src 'self' 'unsafe-inline'; " +
              "connect-src 'self' ws: wss:"
          });

          response.end(contents);
        }
      );

      return;
    }

    response.writeHead(404, {
      "Content-Type":
        "text/plain; charset=utf-8"
    });

    response.end("Not found");
  }
);

const wss = new WebSocketServer({
  server,
  path: "/ws",
  maxPayload: 16 * 1024,
  perMessageDeflate: false
});

wss.on("connection", (ws) => {
  ws.isAlive = true;
  ws.authenticated = false;
  ws.userId = null;
  ws.userName = "Anonymous";
  ws.userRole = "Resident";

  ws.on("pong", () => {
    ws.isAlive = true;
  });

  ws.on("message", (raw) => {
    let message;

    try {
      message = JSON.parse(
        raw.toString()
      );
    } catch {
      sendError(
        ws,
        "Invalid JSON message."
      );

      return;
    }

    if (
      !message ||
      typeof message !== "object"
    ) {
      sendError(
        ws,
        "Invalid message."
      );

      return;
    }

    if (message.type === "hello") {
      const suppliedId = cleanString(
        message.userId,
        100
      );

      ws.userId =
        /^[a-zA-Z0-9-]{16,100}$/.test(
          suppliedId
        )
          ? suppliedId
          : crypto.randomUUID();

      ws.userName =
        cleanString(
          message.name,
          MAX_NAME_LENGTH
        ) || "Anonymous";

      ws.userRole =
        process.env.ALLOW_DEMO_ROLES ===
          "true" &&
        VALID_ROLES.has(message.role)
          ? message.role
          : "Resident";

      ws.authenticated = true;

      send(ws, {
        type: "session",
        userId: ws.userId,
        name: ws.userName,
        role: ws.userRole
      });

      sendInitialState(ws);
      return;
    }

    if (!ws.authenticated) {
      sendError(
        ws,
        "Start the session before sending commands."
      );

      return;
    }

    if (message.type === "join") {
      const group = state.groups.find(
        (item) =>
          item.id === message.groupId &&
          item.visibility === "public"
      );

      if (!group) {
        sendError(
          ws,
          "Public group not found.",
          message.requestId
        );

        return;
      }

      const membership =
        membershipFor(ws.userId);

      membership.add(group.id);

      setMembership(
        ws.userId,
        membership
      );

      send(ws, {
        type: "myGroups",
        myGroups: [...membership],
        requestId:
          message.requestId || null
      });

      return;
    }

    if (
      message.type === "joinByCode"
    ) {
      const code = cleanString(
        message.code,
        10
      ).toUpperCase();

      const group = state.groups.find(
        (item) =>
          item.visibility === "private" &&
          item.code === code
      );

      if (!group) {
        send(ws, {
          type: "joinResult",
          ok: false,
          message:
            "No private group has that code.",
          requestId:
            message.requestId || null
        });

        return;
      }

      const membership =
        membershipFor(ws.userId);

      membership.add(group.id);

      setMembership(
        ws.userId,
        membership
      );

      send(ws, {
        type: "groupAdded",
        group
      });

      send(ws, {
        type: "myGroups",
        myGroups: [...membership]
      });

      send(ws, {
        type: "joinResult",
        ok: true,
        message:
          `Joined ${group.name}.`,
        groupId: group.id,
        requestId:
          message.requestId || null
      });

      return;
    }

    if (message.type === "leave") {
      const membership =
        membershipFor(ws.userId);

      membership.delete(
        message.groupId
      );

      setMembership(
        ws.userId,
        membership
      );

      send(ws, {
        type: "myGroups",
        myGroups: [...membership],
        requestId:
          message.requestId || null
      });

      return;
    }

    if (
      message.type === "createGroup"
    ) {
      const name = cleanString(
        message.name,
        MAX_NAME_LENGTH
      );

      if (!name) {
        sendError(
          ws,
          "Group name is required.",
          message.requestId
        );

        return;
      }

      const baseId =
        createGroupId(name) || "group";

      let groupId = baseId;
      let number = 2;

      while (
        state.groups.some(
          (group) =>
            group.id === groupId
        )
      ) {
        groupId =
          `${baseId}-${number}`;

        number += 1;
      }

      let code = makeCode();

      while (
        state.groups.some(
          (group) =>
            group.code === code
        )
      ) {
        code = makeCode();
      }

      const group = {
        id: groupId,
        name,
        metro: "Private group",
        visibility: "private",
        code,
        createdBy: ws.userId,
        createdAt: Date.now()
      };

      state.groups.push(group);

      const membership =
        membershipFor(ws.userId);

      membership.add(group.id);

      setMembership(
        ws.userId,
        membership
      );

      saveStateSoon();

      send(ws, {
        type: "groupAdded",
        group
      });

      send(ws, {
        type: "myGroups",
        myGroups: [...membership]
      });

      send(ws, {
        type: "createdGroup",
        group,
        requestId:
          message.requestId || null
      });

      return;
    }

    if (message.type === "send") {
      const group = state.groups.find(
        (item) =>
          item.id === message.groupId
      );

      if (!group) {
        sendError(
          ws,
          "Group not found.",
          message.requestId
        );

        return;
      }

      if (
        !isMember(
          ws.userId,
          group.id
        )
      ) {
        sendError(
          ws,
          "Join this group before posting.",
          message.requestId
        );

        return;
      }

      const text = cleanString(
        message.text,
        MAX_MESSAGE_LENGTH
      );

      if (!text) {
        sendError(
          ws,
          "Message text is required.",
          message.requestId
        );

        return;
      }

      const kind =
        message.kind === "incident"
          ? "incident"
          : "chat";

      let peril;

      if (kind === "incident") {
        peril = VALID_PERILS.has(
          message.peril
        )
          ? message.peril
          : "Suspicious Activity";
      }

      const record = {
        id: crypto.randomUUID(),
        groupId: group.id,
        kind,
        authorId: ws.userId,
        author: ws.userName,
        role: ws.userRole,
        text,
        peril,
        location:
          kind === "incident"
            ? cleanString(
                message.location,
                MAX_LOCATION_LENGTH
              )
            : undefined,
        ts: Date.now()
      };

      state.messages.push(record);

      if (
        state.messages.length >
        MAX_MESSAGES
      ) {
        state.messages.splice(
          0,
          state.messages.length -
            MAX_MESSAGES
        );
      }

      saveStateSoon();

      broadcastToGroup(
        group.id,
        {
          type: "message",
          message: record,
          requestId:
            message.requestId || null
        }
      );

      return;
    }

    sendError(
      ws,
      "Unknown command.",
      message.requestId
    );
  });

  ws.on("error", (error) => {
    console.error(
      "WebSocket client error:",
      error.message
    );
  });
});

const heartbeat = setInterval(
  () => {
    for (const ws of wss.clients) {
      if (!ws.isAlive) {
        ws.terminate();
        continue;
      }

      ws.isAlive = false;
      ws.ping();
    }
  },
  30000
);

server.listen(
  PORT,
  HOST,
  () => {
    console.log(
      `Sentinel chat listening on http://${HOST}:${PORT}`
    );

    console.log(
      `Available public groups: ${PUBLIC_GROUPS.length}`
    );

    console.log(
      SYSTEM_KEY
        ? "System announce endpoint enabled."
        : "SENTINEL_SYSTEM_KEY unset - /system/announce disabled."
    );
  }
);

function shutdown(signal) {
  console.log(
    `${signal} received. Shutting down.`
  );

  clearInterval(heartbeat);
  clearTimeout(saveTimer);

  saveStateImmediately();

  for (const ws of wss.clients) {
    ws.close(
      1001,
      "Server shutting down"
    );
  }

  server.close(() => {
    process.exit(0);
  });

  setTimeout(() => {
    process.exit(1);
  }, 10000).unref();
}

process.on(
  "SIGTERM",
  () => shutdown("SIGTERM")
);

process.on(
  "SIGINT",
  () => shutdown("SIGINT")
);