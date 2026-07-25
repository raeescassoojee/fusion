# Sentinel Mesh deployment

```
CloudFront  https://xxxx.cloudfront.net    free valid cert, no domain needed
     |
   EC2 :80  nginx
     |-- /           -> FastAPI :8000   dashboard + ops API
     |-- /chat/      -> Node    :8080
     `-- /feedback/  -> Node    :8090
```

CloudFront is not optional. Browsers block `getUserMedia` on plain HTTP, so on a
bare EC2 IP your Live AI camera page is dead. CloudFront supplies HTTPS on a
`*.cloudfront.net` domain with no domain purchase, and passes WebSockets.

Your DynamoDB tables and S3 bucket already exist in account `426421369712`
(`eu-west-1`). Nothing needs re-provisioning.

---

## Before you launch

**1. Push your work.** The instance clones from GitHub, so anything uncommitted
locally will not exist on the server.

```powershell
cd D:\Desktop\fusion
git add -A
git commit -m "chat integration, activity feed, rekognition"
git push origin main
```

**2. If the repo is private**, either make it public for the hackathon or bake a
deploy token into `REPO_URL` in `bootstrap.sh`.

**3. Set a real plate salt.** Edit `bootstrap.sh` and replace
`CHANGE-ME-LONG-RANDOM`, or export `SENTINEL_PLATE_SALT` before launching.

---

## Launch

```powershell
.\deploy\launch-ec2.ps1
```

Creates a key pair, a security group (SSH from your IP, HTTP from anywhere) and
a `t3.medium` running the bootstrap as user-data. Bootstrap takes 5-10 minutes -
OpenCV and the ONNX models are the slow part.

Watch it:

```powershell
ssh -i sentinel-key.pem ubuntu@<IP> "sudo tail -f /var/log/cloud-init-output.log"
```

You want `BOOTSTRAP COMPLETE`. Then `http://<IP>/health` should answer.

**`sentinel-key.pem` must never be committed.** Add it to `.gitignore` now.

## HTTPS

```powershell
.\deploy\create-cloudfront.ps1 -OriginDns <public DNS from launch output>
```

5-15 minutes to deploy. Then test **camera capture on the HTTPS URL** - that is
the whole reason CloudFront is here.

---

## Redeploying while your teammate iterates

```powershell
ssh -i sentinel-key.pem ubuntu@<IP>
sudo -u sentinel git -C /opt/sentinel pull
sudo systemctl restart sentinel-ops sentinel-chat sentinel-feedback
```

About ten seconds. Caching is disabled on the distribution, so a browser refresh
shows UI changes immediately - no invalidation needed.

Note the bootstrap patches `client.html` to use `/chat/ws`. A `git pull` that
overwrites that file breaks chat until you re-apply:

```bash
sed -i 's#${protocol}//${location.host}/ws#${protocol}//${location.host}/chat/ws#' \
  /opt/sentinel/services/chat/client.html
```

Better: commit that change to the repo so it survives pulls.

---

## Troubleshooting

```bash
sudo systemctl status sentinel-ops
sudo journalctl -u sentinel-ops -n 50 --no-pager
sudo nginx -t
curl localhost:8000/health ; curl localhost:8080/health ; curl localhost:8090/health
```

- **502 on `/`** - ops service down, check journalctl
- **Chat connects then drops** - `/chat/ws` path or the WS upgrade headers
- **Feedback has 0 locations** - it cannot see `services/claims/data/curated/hotspots.json`
- **`/api/aws/status` not ready** - EC2 has no credentials; either attach an
  instance role with DynamoDB + S3 access, or put keys in `/etc/sentinel.env`.
  An instance role is the correct choice.

---

## Teardown - do this after judging

This is Discovery's account and these resources bill by the hour.

```powershell
aws cloudfront get-distribution-config --id <ID> --profile sentinel-discovery
# set Enabled=false, update, wait for Deployed, then delete
aws ec2 terminate-instances --instance-ids <ID> --region eu-west-1 --profile sentinel-discovery
```

Leave the DynamoDB tables and S3 bucket if you want the evidence to persist;
otherwise `python provision_aws.py --region eu-west-1 --teardown`.
