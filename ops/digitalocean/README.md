# DigitalOcean automatic deployment

Production: https://143-198-87-243.sslip.io
Repository branch: maison-lumiel. App build context: junyeong-classroom-luxe.

The root-owned systemd timer checks the public GitHub branch every 60 seconds. It builds only when the revision changes. No GitHub token or SSH key is required. Only trusted maintainers should be able to push to this deployment branch; its Dockerfile and application code run on production.

Install once from the existing DigitalOcean root console, after current uploads finish. Download install.sh from a reviewed commit, then invoke `bash install.sh COMMIT_SHA`. The installer fetches deploy.sh from that same pinned revision. It enables maison-autodeploy.timer and starts the first deployment.

The live application always mounts /srv/maison-lumiel/data at /data and reads /srv/maison-lumiel/spaces.env. These are not checked out from GitHub. Builds happen while the old container runs. A short stop is needed to snapshot SQLite and switch containers. SQLite backup API snapshots and secret.key are retained with mode 0700/0600 under /opt/maison-autodeploy/backups. These are local snapshots, not protection against loss of the Droplet.

A build failure leaves production untouched. A failed startup or public health check restores the previous container. Automatic rollback restores the code/container only, not database schema or data; destructive schema migrations require a separate reviewed migration/restore procedure. Healthy deployment requires both API health and an existing admin account. Backups, previous containers, and image layers are retained; monitor disk space and prune deliberately after verifying restore points.

Inspect: `systemctl list-timers maison-autodeploy.timer --no-pager`
Logs: `journalctl -u maison-autodeploy.service -n 50 --no-pager`
Deployed revision: `cat /opt/maison-autodeploy/deployed`
Pause: `systemctl stop maison-autodeploy.timer`
Disable permanently: `systemctl disable --now maison-autodeploy.timer`
Resume: `systemctl enable --now maison-autodeploy.timer`
Retry a failed revision: remove /opt/maison-autodeploy/failed and start maison-autodeploy.service, or push a fix.

A failed revision is not repeatedly rebuilt every minute. A new commit retries automatically. Updating the tracked deployment scripts does not silently replace the root-owned runner; reinstall a reviewed pinned revision to update the runner itself.
