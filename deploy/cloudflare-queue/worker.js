export default {
  async fetch(req, env) {
    if (req.headers.get("Authorization") !== `Bearer ${env.QUEUE_KEY}`) return new Response("no", { status: 401 });
    if (req.method === "POST") {
      const cmd = await req.json();
      cmd.id ||= crypto.randomUUID();
      cmd.expires_at ||= new Date(Date.now() + 15 * 60e3).toISOString();
      await env.Q.put("cmd:" + Date.now() + ":" + cmd.id, JSON.stringify(cmd), { expirationTtl: 3600 });
      return Response.json({ ok: true, id: cmd.id });
    }
    const list = await env.Q.list({ prefix: "cmd:" });
    const commands = [];
    for (const k of list.keys) { const v = await env.Q.get(k.name); if (v) commands.push(JSON.parse(v)); await env.Q.delete(k.name); }
    return Response.json({ commands });
  },
};
