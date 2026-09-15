# Workspace UI in the desktop app

The launcher ships the web Workspace interface from `workspace/frontend`.
Change workspace lists, creation, chat, files, and settings in those shared
pages and components. Do not add a second Workspace UI to the launcher.

`workspace/frontend/desktop/app.tsx` composes the same pages with a lightweight
hash router and aliases for Next.js client APIs. `build:desktop` bundles that
entry for Electron. The launcher serves the bundle through
`openagents://workspace` in an owned `WebContentsView`. API data still requires
the configured workspace service.

The window has two halves, switched from the mode bar in a fixed place:
Workspace and This Computer. The launcher owns the mode bar, the This Computer
tools, and the signed-out Workspace — Welcome and native sign-in. Signing in
opens the shared membership home. Local agent setup works without an account.
Opening a connected workspace from This Computer stays in the app when signed
in to the same deployment; the browser remains a menu option.

There is no device-pairing or in-app "connect a workspace" flow any more — a
workspace is created on the web after signing in, and an agent connects to it
from the command line with `agn connect <agent-name> <workspace-token>`, which
registers the workspace as a network this device already knows about. The
Workspaces page and the agent Connect dialog only ever list networks
registered that way; neither offers a way to add one.

Both Welcome and the email sign-in form open native email registration. It uses
the existing `POST /v1/auth/register` account endpoint, followed by the same
Workspace handoff and session redemption as sign-in. The fields and password
policy match [the account website](https://openagents.org/signup) (verified
September 13, 2026). Registration never falls back to creating a Firebase user.
If registration succeeds but session redemption fails, the form offers sign-in
and explains that the account already exists.

New profiles start in light mode, shared by the native window and Workspace.
The appearance setting still supports dark mode and following the system.
Existing stored choices take precedence over the default.

The welcome illustration is a screenshot of the shared Workspace components
with synthetic content, in both languages and themes. Regenerate it after
Workspace design changes by running `node scripts/render-workspace-preview.mjs`
from `packages/launcher` after building the desktop bundle. The renderer uses
an isolated browser and blocks external requests; the data fixture lives beside
the script. No separate Workspace layout is maintained for the illustration.

`lib/desktop-host.ts` is the shared UI's optional bridge for sign-in, sign-out,
and opening This Computer. It returns null on the web. The desktop preload
supplies the account session, endpoint, and appearance. Appearance sync remains
in `desktop/host.ts`.

Main is the only owner of the account session. The preload plants it when the
page loads and forwards renewals through `onSession`; the page never reports its
own storage back, so nothing it does to that storage can sign the app out.
Signing in and out from the page are requests to main. However an account ends —
sign-out on either side, expiry, a refused renewal — main destroys the view and
clears its storage, and waits for that before creating another view.

The view is drawn above the launcher's DOM. Launcher toasts raised while it is on
screen are repeated inside it through `onNotice`, and the update banner sits in
the mode bar instead of floating over the content area.

Switching to This Computer hides the web view while keeping its live state.
On relaunch, the desktop router restores the last route for the signed-in
account, and the layout restores the workspace view and selected thread.
Query strings and access tokens are excluded from saved navigation. Local
navigation is remembered separately. Sign-out destroys the web view and clears
its browser storage.

For development, build the shared bundle before starting Electron:

```sh
npm --prefix workspace/frontend run build:desktop
npm --prefix packages/launcher run dev
```

Rebuild the desktop bundle after shared web changes. Restart Electron after
changing main or preload code; a renderer refresh cannot update those bridges.
The launcher production build runs the shared build automatically, then
`scripts/check-workspace-bundle.mjs` fails the build if the bundle is missing.
CI installs the shared frontend's dependencies through
`.github/actions/workspace-frontend-deps`, and the `Workspace Desktop Bundle`
workflow builds the bundle on pull requests that touch it.

Without a bundle, a dev build shows the hosted Workspace so other launcher work
can continue; This Computer actions are unavailable there, because the hosted
page is not the bundle's origin. An installed app refuses to do this and shows
a reinstall message instead.

The API must allow the bundle's origin: `CORS_ORIGINS` needs
`openagents://workspace`. Until the deployment has it, `allowBundleApiAccess`
rewrites the CORS headers for requests the bundle makes.

## Local agent management

Placement AI does not install, catalog, or authenticate third-party coding-CLI
tools — that whole surface (the Agent Marketplace rail entry, the shared
`workspace/frontend/components/agents/agent-setup.tsx` picker it used to embed
via `pages/agents/local-agent-setup.tsx`, and the matching
`e2e/shared-agent-setup.spec.ts`) was removed together with the connector's
catalog/install API. The launcher renderer no longer aliases `@/` to the shared
frontend package; nothing under `src/renderer` depends on it.

What remains is generic: `pages/agents/index.tsx` lists the daemon's named
agent instances (any type — there is no more per-type catalog) and
`pages/agents/components/manage-agent-dialog.tsx` is the whole "add or
reconfigure one" flow — a name and a working directory, via the connector's
plain `addAgent` / `setAgentWorkingDir`. This Computer still opens a device
overview with its agents and connected workspaces; there is no separate Agents
page and no Agent Marketplace to browse.
