# Campus backend phase 1 operations

This overlay deploys the Campus backend beside, not over, the current port-80
upstream Dify. Its Compose project is `njit-campus`; the default canary is
`127.0.0.1:13000` for gateway administration and loopback port `18080` for
Dify during administrator bootstrap. The gateway database and Redis have no
host ports.

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
- The student allowance contract returns only RMB remaining/used/total and
  per-model usage. Gateway tokens, channels, upstream credentials, and internal
  price expressions are never returned.

Frontend work, assignment submission, grading, groups, teacher Dify, payments,
and real roster/SSO data are outside this phase.

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

Use an SSH tunnel to `127.0.0.1:18080` while creating the initial Dify
administrators. Do not bind the Campus route to a campus interface until setup
is complete and both administrator logins have been verified.

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
9. Verify the existing port-80 Dify route before and after every canary change.

The stock Dify shell remains visible in this backend-only phase, but its
workspace APIs are protected. The Campus access frontend will replace that
shell later without changing these backend contracts.

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

## HTTPS

HTTPS is preferred. Until campus certificates are available, HTTP is accepted
only on the campus intranet: keep the gateway loopback-only, use HttpOnly/Lax
portal cookies, prohibit internet exposure, and record the exception. When TLS
is enabled, set `NGINX_HTTPS_ENABLED=true`, install the certificate/key, and set
`CAMPUS_PORTAL_COOKIE_SECURE=true` before acceptance.
