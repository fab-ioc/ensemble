# Feedback relay

The CEO deploys this; the hub never receives its GitHub token. Any Ensemble
installation can use the deployed HTTPS URL. No deployment is performed by the
task. Cloudflare's Workers Free plan supports the SQLite Durable Object used
for the rate counter ([limits](https://developers.cloudflare.com/durable-objects/platform/limits/)).

## Deploy

1. Create/sign into a Cloudflare account, install Node.js, and enable Workers.
2. In GitHub create a fine-grained PAT for **only `fab-ioc/ensemble`**, with
   **Issues: read and write** (and mandatory Metadata read). GitHub has no
   create-only issue permission. Prefer a dedicated bot account with access to
   that repository; its account is the author of relayed issues, not the sender.
   See [GitHub's create-issue permissions](https://docs.github.com/en/rest/issues/issues#create-an-issue).
   Create the labels `feedback`, `bug`, `idea` in the repo first. Issues must be enabled.
3. From this folder, run the following. Paste the PAT at the first secret prompt,
   and a password-manager-generated random secret (32+ bytes) at the second.

```sh
npx wrangler@4 login
npx wrangler@4 secret put GITHUB_TOKEN
npx wrangler@4 secret put IP_HASH_SECRET
npx wrangler@4 deploy
```

4. Put the printed `https://ensemble-feedback.<account>.workers.dev` URL into
   Ensemble **Settings → Feedback relay URL**. To ship it as the default for all
   future installs, change `feedback.DEFAULT_RELAY` to that public URL and release.
   The default is deliberately empty until deployment. A custom repo requires
   changing `FEEDBACK_REPO` in `wrangler.jsonc`, matching hub Settings, token access,
   and redeployment. Rotate an expired PAT with the same `secret put` command.

## Privacy and operational limits

- Three requests per UTC hour per **hub egress IP** (all devices on that hub share
  it). A secret-keyed HMAC rotated hourly identifies only the durable counter.
  Atomic transactions prevent concurrent bypass. An alarm deletes counts within
  two hours. Raw IPs and feedback content are never stored or logged by this code;
  Workers observability is disabled. Cloudflare still processes network traffic,
  and GitHub retains the posted issue. Do not enable request logging or `wrangler tail`.
- 32 KB maximum request, 24 KB UTF-8 body, 200-character title; streaming checks
  also enforce the cap without `Content-Length`. Repo is fixed server-side.
- The hub removes known identities, paths, email addresses, tokens and links
  from anonymous text before preview. Arbitrary prose can still identify someone.
  No image uploads: an Issues-only token cannot upload image files. Pasted images
  remain in the dialog for manual attachment by named submitters on GitHub;
  anonymous issues omit them entirely. No image names reach the relay.
- No relay secret or hub credential is sent by clients. This public endpoint can
  receive spam; per-IP limits are not a global anti-abuse guarantee. Free-plan
  quotas can make it unavailable. Monitor the repository, not request contents.
- A timeout may happen after GitHub creates an issue. The UI keeps the text and
  asks users to check the repository before retrying. Browser fallback requires
  login and is **not anonymous**, so opening it requires a separate explicit click.
  Long prefilled URLs can exceed a browser's limit; the preview remains copyable.

## Tests

```sh
node --test relay/feedback/worker.test.mjs
```

Run from the repo root. Tests mock GitHub and Durable Object storage; no credentials
or live issues are used.
