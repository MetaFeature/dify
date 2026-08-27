# Campus platform operations

This overlay first deploys the Campus backend beside, not over, the current
port-80 upstream Dify. After acceptance, its reversible promotion workflow
makes the Access portal the campus-intranet front door and preserves the
unchanged upstream Dify nginx on host loopback. The Access portal is a separate,
thin frontend container; the existing Campus backend remains inside the Dify
API container. The Compose project is `njit-campus`; the default canary is
`127.0.0.1:13000` for gateway administration and loopback port `18080` for
Dify during administrator bootstrap. The gateway database, Redis, Dify data
services, and plugin daemon have no host ports.

The Campus API image layers the committed Campus source over the pinned
official Dify 1.16.0 API image. The base-image digest is validated before every
deployment; a registry mirror is allowed only when it retains that digest.

## Implemented boundary

- One normalized student identity maps lazily to one Dify account, one Dify
  workspace, one hidden gateway user, and one hidden gateway token.
- A service-principal account owns every student workspace; the student is an
  Editor. Campus administrators are not silently added as workspace members.
- The identity adapter supports virtual acceptance identities now. Excel and
  SSO remain fail-closed interfaces until their real schemas are supplied.
- UTC+8 has twelve fixed two-hour slots per day. The rolling seven-day window,
  capacity 500, one unfinished booking, FIFO waitlist, pre-start cancellation,
  promotion, and slot-time access are enforced in the database service.
- A zero model allowance blocks gateway calls but does not block Dify access,
  editing, viewing, or export during a confirmed slot.
- Each student workspace installs the pinned official OpenAI provider plugin
  before the managed NewAPI credential is validated and stored. Plugin package
  identity is explicit configuration so deployments do not drift to an
  unreviewed Marketplace release during provisioning.
- The student allowance contract returns only RMB remaining/used/total and
  per-model usage. Gateway tokens, channels, upstream credentials, and internal
  price expressions are never returned.

Assignment submission, grading, groups, teacher Dify, payments, and real
roster/SSO data remain outside the implemented boundary.

## First canary deployment

1. Keep the current port-80 deployment running and take its normal independent
   backup. This overlay never uses its project, databases, networks, volumes,
   secrets, or host ports.
2. Copy the normal Dify `.env.example` to `.env`, generate fresh Campus-only
   Dify secrets, then copy `docker/envs/campus.env.example` to
   `docker/envs/campus.env`, set mode `0600`, and replace every placeholder.
   The management script loads `.env` first and the protected Campus file
   second, so the Campus ports and controls take precedence.
3. Run `docker/campus/manage.sh validate`.
4. Run `docker/campus/manage.sh gateway-up`. It starts only the gateway
   database, Redis, and gateway and does not require the not-yet-created admin
   token. Its administrator UI is loopback-only; use an SSH tunnel to
   `127.0.0.1:13000` for first setup.
5. In the isolated gateway, create the root/admin, add the `campus` group,
   configure the allowed text/vision/image/audio channels and RMB-per-million
   token pricing, then create an administrator personal access token. Put only
   that token and user ID in `campus.env`.
6. Start the Dify database and core services, then run
   `docker/campus/manage.sh open-bootstrap`. This command requires Dify port
   `18080` to remain loopback-only, takes a backup, and opens the one-time setup
   page without disclosing an initialization password. Complete initial Dify setup,
   create the service-principal account named in `CAMPUS_SERVICE_PRINCIPAL_EMAIL`,
   install the configured OpenAI-compatible provider, and put the first Dify
   administrator account ID in `CAMPUS_BOOTSTRAP_ADMIN_ACCOUNT_IDS`.
7. Run `docker/campus/manage.sh deploy`, then `docker/campus/manage.sh verify`.

The Access portal is served at `/portal/` on the Campus origin. It holds no
credentials or durable business state and is attached only to the internal
`campus_portal` network. Campus nginx bridges that network to the existing Dify
network, so browsers call `/console/api/campus/*` with same-origin cookies while
the portal container has no host port and no direct API-network access.

The campus-facing listener blocks stock Dify sign-in, signup, password-reset,
activation, and login APIs. Named administrators use the separate
host-loopback-only listener at `127.0.0.1:${CAMPUS_ADMIN_PORT:-18081}` (normally
through an SSH tunnel). This preserves the upstream administration surface
without exposing a second student authentication path.

The portal is intentionally a Chinese-only, framework-independent static
surface. It does not modify or import the upstream Dify `web/` application;
dynamic portal copy is owned by `portal/assets/messages.js`. This is the
localization boundary for the isolated portal rather than Dify's
`web/i18n/*` catalogs.

Use an SSH tunnel to the host-loopback administration listener at
`127.0.0.1:18081` while creating the initial Dify administrators. Do not bind
the Campus route to a campus interface until setup
is complete and both administrator logins have been verified.

After administrator setup and full canary acceptance, keep port `18080` as a
loopback verification route. From an elevated Windows shell, restrict TCP 80
through both Windows Firewall and the WSL Hyper-V firewall before promotion:

```powershell
.\campus\windows\configure-intranet-firewall.ps1 -Action Apply -Port 80 -RemoteAddress 10.0.0.0/255.0.0.0
```

Then run `docker/campus/manage.sh promote`. The command takes a Campus backup,
rebinds the upstream nginx to `127.0.0.1:18082`, publishes Campus nginx on
`10.20.10.193:80`, retains `127.0.0.1:18080` for health checks, and verifies
that `/` redirects to `/portal/`. It fails closed and restores the upstream
entry if Campus verification fails.

`-Action Verify` is idempotent firewall verification. For rollback, run
`docker/campus/manage.sh rollback-promotion` first, then use firewall
`-Action Remove`; this restores the previous port-80 rule state from the latest
port-specific backup. Firewall backups are stored under
`C:\ProgramData\NJITCampus\firewall-backups`.

If Docker Hub is unavailable, set the three `CAMPUS_GATEWAY_*_IMAGE`
variables to an approved registry mirror while retaining the documented image
digests. The build remains reproducible and does not require changing the
Docker daemon used by the port-80 baseline. If `proxy.golang.org` is not
reachable, `CAMPUS_GATEWAY_GO_PROXY=https://goproxy.cn,direct` is the supported
fallback; module checksums remain enforced by Go.

Do not expose port 13000 on a campus interface. The gateway name, administrator
token, user IDs, channel details, and upstream credentials are operational
secrets and must not appear in student responses or logs.

## Acceptance flow

Use a virtual identity from the protected environment file:

1. `POST /console/api/campus/auth/virtual` and retain the portal cookie.
2. Verify the student, workspace, managed gateway user/token, and provider
   credential are created once. Repeat login and verify all IDs are unchanged.
3. `GET /console/api/campus/slots?day=YYYY-MM-DD`; verify twelve UTC+8 slots.
4. Reserve a future slot. Fill a test slot to capacity in an isolated test run,
   verify FIFO waitlisting, cancel a confirmed booking, and verify promotion.
5. Before the slot, `/console/api/campus/session/launch` must be rejected. At
   the slot start it must establish a Dify session in exactly that student's
   workspace. A different student must not see the workspace.
6. Import a teacher-provided `.yml`, edit it, and export it again. No assignment
   submission or grading controls should be present.
7. Exhaust the model allowance. Model calls must fail at the gateway while the
   workspace remains usable and export still works.
8. After the slot end, the nginx authorization subrequest must reject further
   interactive workspace API requests even if the Dify cookie remains valid.
9. Before promotion, verify the existing port-80 Dify route. After promotion,
   verify `http://10.20.10.193/` redirects to `/portal/`, while the unchanged
   upstream Dify responds only on `127.0.0.1:18082`.

The Access portal is the student-facing entry point. The stock Dify shell and
bootstrap login routes remain reachable for named Campus administrators; Campus
student accounts use internal, non-routable addresses, receive no passwords,
and can obtain a Dify session only through the portal launch API during an
active reservation. All workspace APIs remain protected by the authorization
subrequest after launch.

For the demonstration gate, configure exactly two virtual identities in the
protected `campus.env`, synchronize those identities through the Campus
administrator service, and activate two individually named, password-enabled
Dify accounts as Campus administrators. Run
`docker/campus/manage.sh verify-demo-accounts` to compare the protected virtual
roster with active Campus rows, require a successful login for both named
administrators and a current portal session for both students, and print only
account names; it never prints emails, password hashes, login codes, or tokens.

## Backup and rollback

`manage.sh deploy` creates a mode-`0700` backup before changing an existing
Campus project. `manage.sh backup` can be run explicitly. Each backup
contains clean/re-creatable dumps for each database that is already running,
Dify file/plugin/vector data, the protected configuration, the configured-image
and container inventory, and checksums. A gateway-only bootstrap backup
therefore has no Dify dump yet.
Treat the backup directory as secret-bearing data.

The script also detects stopped Compose containers and existing PostgreSQL
volumes. It fails closed instead of rebuilding from stopped database state:
start the existing `db_postgres` and/or `model-gateway-db`, run `manage.sh
backup`, then retry deployment. The same gate applies when rerunning
`gateway-up` against an existing gateway volume.

Before a cutover, rollback is simply:

```sh
docker/campus/manage.sh stop
curl --fail http://127.0.0.1/
```

No port-80 service was changed. For an upgrade rollback, stop the Campus
project, verify `SHA256SUMS`, restore the protected config and image tags from
the selected backup, pipe each `--clean --if-exists` SQL dump into its matching
PostgreSQL container, restore `dify-files.tgz`, then start and repeat the full
acceptance flow. Preserve the failed state until the restored stack is proven;
do not delete volumes during rollback.

After front-door promotion, use the deterministic runtime rollback instead:

```sh
docker/campus/manage.sh rollback-promotion
```

Then remove the Campus TCP-80 firewall rules from an elevated Windows shell.
The upstream nginx returns to its original public port without changing its
database, network, volume, image, or application configuration.

## HTTPS

HTTPS is preferred. Until campus certificates are available, HTTP is accepted
only on the campus intranet: keep the gateway loopback-only, use HttpOnly/Lax
portal cookies, prohibit internet exposure, and record the exception. When TLS
is enabled, set `NGINX_HTTPS_ENABLED=true`, install the certificate/key, and set
`CAMPUS_PORTAL_COOKIE_SECURE=true` before acceptance.
