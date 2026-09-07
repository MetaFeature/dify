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
- The identity adapter consults platform-held student credentials first
  (administrator-imported or reset passwords, hashed at rest); the virtual
  acceptance roster remains a temporary fallback for students without a
  credential row. Excel and SSO remain fail-closed interfaces until their
  real schemas are supplied.
- UTC+8 has twelve fixed two-hour slots per day. The rolling seven-day window,
  per-slot administrator-adjustable capacity (default 500, zero closes a
  slot), the one-effective-plus-one-pending claim rule, FIFO waitlist,
  cancellation until slot end (an in-progress cancellation revokes access
  immediately), promotion, and slot-time access are enforced in the database
  service.
- The current in-progress slot accepts a supplemental reservation only while
  fixed capacity remains and the one-minute system load per logical CPU is at
  or below `CAMPUS_CURRENT_SLOT_MAX_LOAD_PER_CPU`. Missing or invalid load data
  fails closed. A full current slot still creates a FIFO waitlist entry, and
  every current-slot claim ends at the original slot boundary.
- Every roster student receives a hidden NewAPI user, managed token, and
  planned allowance before first login. The Dify workspace remains lazy and
  adopts that token without resetting its balance. A zero model allowance
  blocks gateway calls but does not block Dify access,
  editing, viewing, or export during a confirmed slot.
- Each student workspace installs the pinned official OpenAI-API-compatible
  provider before the managed NewAPI credential is validated and stored on
  each configured custom model. The compatible plugin has no provider-level
  credential schema: its model fields are `api_key` and `endpoint_url`, with
  chat models selecting Chat Completions explicitly. Plugin package identity
  is pinned so provisioning cannot drift to an unreviewed Marketplace release.
- The public nginx reservation check forwards only the Campus portal session
  cookie. A remembered stock Dify session, including an administrator session,
  cannot satisfy the public gate; administrators continue to use the dedicated
  host-loopback listener.
- The student allowance contract returns only USD remaining/used/planned-total and
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
   When rotating `REDIS_PASSWORD`, percent-encode the same value in
   `CELERY_BROKER_URL`; validation rejects a stale Celery credential before
   deployment without printing either value.
   Copy the provider package identity from
   `docker/campus/approved-provider-plugin.txt`; this is the single reviewed
   source for `CAMPUS_MODEL_PROVIDER_PLUGIN_UNIQUE_IDENTIFIER`.
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

An existing Campus deployment that still uses the native OpenAI provider must
run `docker/campus/manage.sh migrate-provider-config` before `validate` or
`deploy`. The command accepts only the known legacy schema, creates a mode-0600
rollback copy of `campus.env`, and rewrites only the provider identity, package
pin, credential scope, and two non-secret field names. Repeating it is a no-op.
To roll back, restore both the prior source revision and the reported protected
environment copy before recreating the API container.

After that source-level migration, run
`docker/campus/manage.sh reconcile-model-providers` once. The command creates a
full runtime backup, copies the existing encrypted managed gateway key into any
missing OpenAI-compatible model records without decrypting or validating it
against a billable model, rewrites legacy OpenAI and DeepSeek workflow/default
references, and uninstalls those two legacy plugins from student workspaces.
The command and its audit are idempotent. Roll back by restoring the source and
the runtime backup path printed by the command; do not restore only one side of
the provider migration.

## Non-billable 100-user baseline

Campus runs two gevent API workers with 200 worker connections each and nginx
uses bounded keepalive pools for its API, Portal, Web, and plugin upstreams.
This leaves headroom above 100 clients and prevents short-lived proxied requests
from exhausting the WSL ephemeral-port range while leaving WebSocket and SSE
connections long-lived.

Run the operational baseline after deployment:

```sh
docker/campus/manage.sh baseline
```

The command first validates every current workspace binding: each student,
Dify account, and tenant must be unique; every tenant must contain exactly the
shared service principal as Owner and its student as Editor; a student account
must belong to no other tenant; and named administrators must not be members.
It then exercises only credential-free Campus routes with synchronized bursts
from 100 virtual users at one request per second each for five minutes. It invokes no model and
fails on an unexpected response, p99 above 100 ms, container restart, unhealthy
canary, or a new nginx `Cannot assign requested address` error.

The API capacity is controlled by `CAMPUS_API_WORKER_AMOUNT` and
`CAMPUS_API_WORKER_CONNECTIONS`; the baseline defaults are controlled by `CAMPUS_BASELINE_CONCURRENCY`,
`CAMPUS_BASELINE_REQUESTS_PER_USER`, `CAMPUS_BASELINE_DURATION_SECONDS`, and
`CAMPUS_BASELINE_MAX_P99_MS`. Treat this as an HTTP/gate baseline, not evidence
for provider latency, model throughput, or campus Wi-Fi reachability.

The Access portal is served at `/portal/` on the Campus origin. It holds no
credentials or durable business state and is attached only to the internal
`campus_portal` network. Campus nginx bridges that network to the existing Dify
network, so browsers call `/console/api/campus/*` with same-origin cookies while
the portal container has no host port and no direct API-network access.

The campus-facing listener sends the exact stock Dify `/signin` route back to
`/portal/` so upstream logout cannot strand a student on an unavailable page.
It also intercepts the exact stock logout API and delegates it to the Campus
backend, which revokes the server-side portal session and clears both the Dify
and `campus_portal_session` cookies. Revisiting a historical Dify URL therefore
requires the student to enter portal credentials again, even while the original
reservation is still effective. Nested sign-in routes, signup, password-reset,
activation, and login APIs remain blocked. Named administrators use the separate
host-loopback-only listener at `127.0.0.1:${CAMPUS_ADMIN_PORT:-18081}` (normally
through an SSH tunnel). This preserves the upstream administration surface
without exposing a second student authentication path.

The Campus Compose overlay renders the sandbox's `SANDBOX_API_KEY` into both
API and worker `CODE_EXECUTION_API_KEY`. `manage.sh validate` rejects any
rendered mismatch without printing either secret, and `manage.sh verify` runs a
non-model CodeExecutor probe so a stale running container cannot leave workflow
Code nodes failing with sandbox `401` responses.

The Campus administration portal owns that loopback listener's root: opening
`127.0.0.1:${CAMPUS_ADMIN_PORT:-18081}/` serves the static page from
`campus/admin/`, while the stock Dify console stays reachable on its own
routes (`/signin` for administrator login, `/apps` for the console itself).
The portal covers per-slot capacity, the student roster (CSV import with a
mandatory preview, single-student entry, suspension, password reset),
allowance adjustments, and named-administrator authorization. It calls
`/console/api/campus/admin/*` with the Dify console session cookies and the
CSRF double-submit header. The roster CSV header is
`student_number,display_name,cohort,password`; the password column sets or
resets credentials only where supplied, and brand-new students must supply
one.

The portal is intentionally a Chinese-only, framework-independent static
surface. It does not modify or import the upstream Dify `web/` application;
dynamic portal copy is owned by `portal/assets/messages.js`. This is the
localization boundary for the isolated portal rather than Dify's
`web/i18n/*` catalogs.

The active Campus route has one user-authorized presentation exception: nginx
serves `campus/branding/njit-logo.png` in place of the bundled Dify logo assets
for both the gated Campus listener and the loopback-only administrator
listener. The source asset is digest-pinned and mounted read-only; the stock
Dify web image and the `dify-mian-20260725` rollback deployment remain
unchanged. Apply only this overlay to an existing Campus project with:

```sh
docker/campus/manage.sh deploy-branding
```

The command validates the asset and Compose configuration, creates a protected
backup, recreates only Campus nginx, and verifies the live logo bytes together
with the normal Campus runtime gates. It prints the protected rollback-backup
directory after success. To roll back the presentation exception, restore the
pre-change tracked Campus source backup and run the restored manager's full
`deploy` command; the earlier manager does not contain `deploy-branding`. This
recreates and verifies the prior Campus configuration without changing the
upstream rollback project or deleting any persistent volume.

Use an SSH tunnel to the host-loopback administration listener at
`127.0.0.1:18081` while creating the initial Dify administrators. Do not bind
the Campus route to a campus interface until setup
is complete and both administrator logins have been verified.

After administrator setup and full canary acceptance, keep port `18080` as a
loopback verification route. From an elevated Windows shell, restrict TCP 80
through both Windows Firewall and the WSL Hyper-V firewall before promotion.
The same reviewed script preserves `%UserProfile%\.wslconfig`, enables mirrored
host-address loopback, and validates the existing `wsl-docker-boot` task:

```powershell
.\campus\windows\configure-intranet-firewall.ps1 -Action Apply -Port 80 -RemoteAddress 10.0.0.0/255.0.0.0
```

Apply the WSL setting without racing the runtime anchor, then verify the real
Windows-to-Campus path:

```powershell
Stop-ScheduledTask -TaskName wsl-docker-boot
wsl.exe --shutdown
Start-ScheduledTask -TaskName wsl-docker-boot
.\campus\windows\configure-intranet-firewall.ps1 -Action Verify -Port 80 -RemoteAddress 10.0.0.0/255.0.0.0
curl.exe --noproxy "*" -I http://10.20.10.193/
```

Docker 29's loopback anti-spoof rules treat mirrored WSL traffic arriving on
`loopback0` as non-loopback traffic. Install the scoped routing reconciliation
after Docker or WSL changes:

```bash
docker/campus/manage.sh repair-loopback-routing
systemctl is-active njit-campus-wsl-loopback-routing.service
sudo /usr/local/sbin/njit-campus-wsl-loopback-routing verify
```

The systemd unit runs after and with `docker.service`. It permits only source
`127.0.0.0/8` traffic arriving on WSL's `loopback0`, only to `127.0.0.1`, and
only for the five private platform ports `13000`, `18080`, `18081`, `18082`,
and `18444`. The rules return those connections to the existing local
`docker-proxy` listeners; they do not publish a private port on a campus
interface.

Windows also has four Public Desktop shortcuts for start, stop, restart, and
status. Install or refresh them from an elevated PowerShell session:

```powershell
.\campus\windows\campus-control.ps1 -Action InstallShortcuts
```

`Start` and `Restart` run focused control-plane health checks. `Stop` stops the
Compose services without removing them, so the boot task and `restart: always`
policies start the platform again at the next Windows boot.

`njit-campus-heartbeat.timer` runs a non-billable component probe every minute.
It checks the API, Portal, worker processes, NewAPI, nginx, container health and
the protected loopback route. Three consecutive failures trigger a scoped
Compose recovery. One-click Stop stops the timer for the current boot without
disabling it, so planned maintenance remains stopped and monitoring returns at
the next boot.

Then run `docker/campus/manage.sh promote`. The command takes a Campus backup,
rebinds the upstream nginx to `127.0.0.1:18082`, publishes Campus nginx on
`10.20.10.193:80`, retains `127.0.0.1:18080` for health checks, and verifies
that an unauthenticated `/` request redirects to `/portal/`. An active,
authorized student session reaches the upstream Dify home page at `/` instead.
Promotion fails closed and restores the upstream entry if Campus verification
fails.

`-Action Verify` is idempotent firewall, WSL-loopback, runtime-anchor, and
Windows HTTP verification. For rollback, run
`docker/campus/manage.sh rollback-promotion` first, then use firewall
`-Action Remove`; this restores the previous port-80 rule and `.wslconfig`
state from the latest port-specific backup. Restart WSL with the task sequence
above after rollback. Firewall backups are stored under
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

## Model pricing

The gateway bills `quota = model_ratio x group_ratio x (prompt_tokens +
completion_tokens x completion_ratio)`, and `QuotaPerUnit` is 500,000 quota per
US dollar. A ratio of `1.0` therefore means $2 per million input tokens.

A model with no ratio entry does **not** fail: `GetModelRatio` returns a
fallback of `37.5` — about $75 per million tokens, roughly five hundred times
the real price — and `SelfUseModeEnabled` decides whether that silently bills or
is rejected. Two invariants keep this away from students, both asserted by
`manage.sh verify`: `SelfUseModeEnabled` stays `false`, and every model enabled
in the gateway's `abilities` table has a price. The practical rule is that a
model is added to a channel's model list only once it is priced.

Prices come from [models.dev](https://models.dev/api.json), preferring the
model's own provider over a reseller. Conversion:

- `ModelRatio` = models.dev `input` / 2
- `CompletionRatio` = `output` / `input`
- `CacheRatio` = `cache_read` / `input`

Ratios are keyed on the name the **student** requests, not the upstream name:
billing reads `GetModelRatio(info.OriginModelName)`, before any channel model
mapping is applied.

| Student-facing model | type | input | output | cache_read | ModelRatio | CompletionRatio | CacheRatio |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `deepseek-v4-flash` | LLM | 0.14 | 0.28 | 0.0028 | 0.07 | 2 | 0.02 |
| `deepseek-v4-flash-0817` | LLM | 0.14 | 0.28 | 0.0028 | 0.07 | 2 | 0.02 |
| `glm-5.3-flash` | LLM | 0.075 | 0.25 | 0.015 | 0.0375 | 3.3333 | 0.2 |
| `bge-m3` | text embedding | 0.02 | n/a | n/a | 0.01 | 0 | n/a |
| `bge-reranker-v2-m3` | rerank | 0.01 | n/a | n/a | 0.005 | 0 | n/a |

Setting the `ModelRatio` option **replaces** the gateway's built-in default
table rather than merging into it, so only the models listed above have a price.
That is deliberate: an unlisted model is unroutable rather than mispriced.

The active Campus catalog contains three LLMs, one text-embedding model and one
reranker. Channel 1 serves `deepseek-v4-flash`. Channel 2 uses the Tianyi
OpenAI-compatible origin and serves `deepseek-v4-flash-0817`,
`glm-5.3-flash`, `bge-m3` and `bge-reranker-v2-m3`. Its former Custom-channel
configuration pointed at the single `/v1/chat/completions` endpoint, which made
embedding and rerank requests arrive as malformed chat traffic. Per-channel
routing now preserves the same credential while using the common HTTPS origin,
so NewAPI selects `/v1/chat/completions`, `/v1/embeddings` or `/v1/rerank` from
the incoming request. Upstream-advertised image and audio model names remain
unpublished until their specific endpoint, Dify model type and price all pass.

Upstream model names are isolated behind the channel's model mapping. The
student-facing name is lowercase and stable; the mapping rewrites it to whatever
the upstream currently calls the model, for example
`{"deepseek-v4-flash": "DeepSeek-V4-Flash"}`. Renaming upstream therefore costs
one mapping edit and touches neither the ratio table nor any student workspace.

### Who configures it, and from where

Upstream credentials, the student-visible model list, and ratios are all
configured in the gateway's own interface. The gateway listens on host loopback
only, exactly like the administration portal, so an administrator reaches it the
same way they reach the portal: on the server itself, or through an SSH tunnel.
The portal's "模型与 API" tab links to it and restates the pricing guardrail.

The NewAPI Users page is the unified accounting view. Every roster student has
a Campus-managed row showing
student number and name, planned total, used quota, remaining quota, request
count, and a synchronization status. Setting a planned total updates the
NewAPI user and the workspace's hidden token in one transaction; it cannot be
set below already-consumed usage. The summary above the table distinguishes the
aggregate planned quota from the per-student amount and also shows raw quota
units. With the current policy it shows 500,000 quota/USD, 10,000,000 planned
quota per student and the aggregate across the roster. It never exposes token
keys.

Nothing about this is exposed on a campus interface. Adding a model to a channel
before giving it a ratio is the one mistake that bills silently, so the order is
always ratio first, model list second.

### Reconciling existing workspaces

A workspace is configured at the student's first sign-in, so widening
`CAMPUS_MODEL_PROVIDER_MODELS` does not by itself reach students who already
have one. The provisioner compares each workspace's registered models against
the configured list on every sign-in and reconciles when they differ, which
costs one query in the common case. The gateway token is fetched again through
create-or-get, which returns the existing token untouched; if it reports having
created a new one, the bound token is gone and provisioning fails rather than
silently restoring the student's spent allowance.

Roster creation and import also run `campus-model-accounts reconcile`. It
creates the hidden NewAPI user/token and `campus_gateway_bindings` row before a
Dify workspace exists, including for suspended identities whose state must be
retained. The later workspace provisioner adopts the existing token and never
reapplies the default allowance. Re-running the reconciliation only refreshes
the student label and verifies the binding.

## Lab manuals

The deep-learning and agent experiment tracks are completed on the student's own
machine, so the only thing the platform publishes for them is a chapter-ordered
lab manual (ADR-0018). The large-model track has no manual: it is completed in
Dify, behind the reservation gate.

Administrators author chapters in the administration portal's "实验手册" tab,
uploading or pasting HTML. Uploads are sanitized before they are stored, once,
so serving a chapter is a plain string read. What is removed:

- scripts, frames, objects, forms, and inputs, together with their content
- stylesheets and inline `style` attributes
- event handler attributes
- links whose protocol is not http, https, or mailto
- images that are not same-origin, because the portal serves `img-src 'self'`

The portal owns how a manual looks, so an administrator controls structure and
content but never presentation. That is deliberate: a chapter exported from a
word processor arrives with hundreds of lines of layout CSS that would break the
page, and the content security policy would drop it silently anyway. The tab
says so, and every save reports what was removed rather than leaving the author
with a page that quietly lost its formatting.

Images are uploaded to the platform from the same tab, which appends an `<img>`
to the chapter body and stores the file through Dify's storage extension. A
remote image cannot work: the portal serves `img-src 'self'`, so the browser
would block it silently. Uploads accept PNG, JPEG, GIF, and WebP up to 4 MB, and
the declared content type must match the file's leading bytes -- the type is
caller-supplied, the bytes are not. SVG is refused: browsers render it as an
image, but it is a document that can carry script, and it would arrive through
the one path that does not sanitize.

Images are served from `/console/api/campus/lab-manuals/images/<id>` to any
signed-in student, and to an administrator's console session for previewing.
That route echoes stored bytes, so it sends `X-Content-Type-Options: nosniff`
and a `default-src 'none'; sandbox` policy: an image must never be able to act
as a page.

A chapter is a draft until it is published; only published chapters reach
students. Reordering renumbers the track, and never crosses into another track.
Deleting a chapter does not delete images it referenced; there is no reference
count, and an orphaned image is cheaper than a chapter with a broken figure.

Authoring writes `lab_manual.chapter_*` audit events. The chapter body is
deliberately absent from them: the trail records what happened, not a second
copy of the document.

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
   workspace and send the browser to `/apps`. A different student must not see
   the workspace. From inside Dify, the Home link to `/` must remain on the
   guarded Dify surface; an unauthenticated `/` request must continue to
   redirect to the Portal.
6. Import a teacher-provided `.yml`, edit it, and export it again. No assignment
   submission or grading controls should be present.
7. Exhaust the model allowance. Model calls must fail at the gateway while the
   workspace remains usable and export still works.
8. After the slot end, the nginx authorization subrequest must reject further
   interactive workspace API requests even if the Dify cookie remains valid.
9. Before promotion, verify the existing port-80 Dify route. After promotion,
   verify from Windows or a campus peer that `http://10.20.10.193/` redirects
   to `/portal/`; do not substitute a WSL self-request to the mirrored host
   address. The unchanged upstream Dify must respond only on
   `127.0.0.1:18082`.

The Access portal is the student-facing entry point. The stock Dify shell and
bootstrap login routes remain reachable for named Campus administrators; Campus
student accounts use internal, non-routable addresses, receive no passwords,
and can obtain a Dify session only through the portal launch API during an
active reservation. All workspace APIs remain protected by the authorization
subrequest after launch.

For the demonstration gate, configure exactly two virtual identities with the
`demo` cohort in the protected `campus.env`, synchronize those identities
through the Campus administrator service, and activate two individually named,
password-enabled Dify accounts as Campus administrators. Other non-demo roster
identities remain outside this gate. Run
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
