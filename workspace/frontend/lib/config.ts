/**
 * Where the hosted Placement AI web app talks to.
 *
 * ONE definition, imported everywhere. This used to be a
 * `process.env.NEXT_PUBLIC_API_URL || '<hardcoded host>'` line copied into six
 * files, which is how three of them were still pointing at the old OpenAgents
 * endpoint after the product moved: changing the origin meant remembering all
 * six, and missing one failed silently at runtime rather than at build.
 *
 * Placement AI is its own product now. The canonical hosted origins are:
 *
 *   app  https://app.placement-ai.com   the application and every auth route
 *   api  https://api.placement-ai.com   the backend
 *
 * NEXT_PUBLIC_* values are inlined at BUILD time, so a deployment sets
 * NEXT_PUBLIC_API_URL in its build environment (docker-compose passes
 * http://localhost:8000 for local development). The constant below is only the
 * fallback for a build that sets nothing.
 */

/** Canonical production API origin. */
export const DEFAULT_API_URL = 'https://api.placement-ai.com';

/** Canonical production application origin — the auth and product origin. */
export const DEFAULT_APP_URL = 'https://app.placement-ai.com';

/** The backend this build talks to. */
export const API_URL = (process.env.NEXT_PUBLIC_API_URL || DEFAULT_API_URL).replace(/\/$/, '');

/**
 * The sign-in page, on this origin.
 *
 * Deliberately a path, not an absolute URL: hosted web, localhost and any
 * self-hosted deployment all serve their own /sign-in, and an absolute origin
 * here is exactly the mistake that sent students to openagents.org/login.
 */
export const SIGN_IN_PATH = '/sign-in';
