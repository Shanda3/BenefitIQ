import { DurableObject } from "cloudflare:workers";

const PORT = 8000;
const INACTIVITY_TIMEOUT_MS = 15 * 60 * 1000;

function containerEnv(env) {
  const out = {
    BENEFITIQ_ENV: "pilot",
    BENEFITIQ_ACCESS_ENFORCE: "1",
    BENEFITIQ_BOOT: "serve",
    FASTEMBED_CACHE_PATH: "/opt/fastembed",
    DATABASE_URL: env.DATABASE_URL,
    ADMIN_EMAILS: env.ADMIN_EMAILS,
    CLOUDFLARE_ACCESS_TEAM_DOMAIN: env.CLOUDFLARE_ACCESS_TEAM_DOMAIN,
    CLOUDFLARE_ACCESS_AUD: env.CLOUDFLARE_ACCESS_AUD,
    BENEFITIQ_SESSION_SECRET: env.BENEFITIQ_SESSION_SECRET,
  };
  for (const name of [
    "TURNSTILE_SITE_KEY",
    "TURNSTILE_SECRET_KEY",
    "ACCESS_NOTIFY_WEBHOOK_URL",
    "OPENAI_API_KEY",
  ]) {
    if (env[name]) out[name] = env[name];
  }
  return out;
}

export class BenefitIQ extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.starting = undefined;
    this.monitoring = undefined;
    const container = ctx.container;
    if (container && container.running) {
      void ctx.blockConcurrencyWhile(() => container.setInactivityTimeout(INACTIVITY_TIMEOUT_MS));
      this.observeExit();
    }
  }

  async fetch(request) {
    if (!this.env.DATABASE_URL) {
      return new Response("The pilot is not configured.", { status: 503 });
    }
    this.starting ??= this.startAndWait().finally(() => {
      this.starting = undefined;
    });
    await this.starting;
    const url = new URL(request.url);
    url.protocol = "http:";
    url.host = "container";
    const forwarded = new Request(url, request);
    forwarded.headers.delete("host");
    return this.ctx.container.getTcpPort(PORT).fetch(forwarded);
  }

  async startAndWait() {
    const container = this.ctx.container;
    if (!container.running) {
      container.start({
        image: container.images.base,
        instance: "basic",
        enableInternet: true,
        env: containerEnv(this.env),
      });
    }
    this.observeExit();
    await container.setInactivityTimeout(INACTIVITY_TIMEOUT_MS);
    const port = container.getTcpPort(PORT);
    for (let attempt = 0; attempt < 90; attempt++) {
      try {
        const response = await port.fetch("http://container/healthz", {
          signal: AbortSignal.timeout(2000),
        });
        await response.body?.cancel();
        if (response.ok) return;
      } catch {
        // The process is still starting.
      }
      await scheduler.wait(1000);
    }
    throw new Error("BenefitIQ did not become ready");
  }

  observeExit() {
    if (this.monitoring || !this.ctx.container) return;
    this.monitoring = this.ctx.container
      .monitor()
      .then(() => console.log("container stopped"))
      .catch(() => console.error("container stopped"))
      .finally(() => {
        this.monitoring = undefined;
      });
    this.ctx.waitUntil(this.monitoring);
  }
}

export default {
  async fetch(request, env) {
    return env.BENEFITIQ.getByName("pilot").fetch(request);
  },
};
