// Telegram Bot API over fetch: HTML messages, retry on 429, Blocked on 403.
export class Blocked extends Error {}

export class Telegram {
  constructor(token, fetchFn = fetch, api = "https://api.telegram.org") {
    this.base = `${api}/bot${token}`; this.fetch = fetchFn;
  }
  async call(method, params) {
    for (let attempt = 0; attempt < 3; attempt++) {
      const res = await this.fetch(`${this.base}/${method}`, {
        method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(params),
      });
      const body = await res.json().catch(() => ({}));
      if (res.ok) return body.result;
      if (res.status === 429) { await new Promise((r) => setTimeout(r, 1000 * Math.min(5, body?.parameters?.retry_after ?? 1))); continue; }
      if (res.status === 403) throw new Blocked(body.description || "forbidden");
      if (res.status === 400 && /not modified/.test(body.description || "")) return null;
      throw new Error(`${method}: ${res.status}`); // never include the URL: it contains the token
    }
    throw new Error(`${method}: rate limited`);
  }
  send(chat, text, kb) {
    return this.call("sendMessage", { chat_id: chat, text, parse_mode: "HTML", reply_markup: kb, link_preview_options: { is_disabled: true } });
  }
  edit(chat, mid, text, kb) {
    return this.call("editMessageText", { chat_id: chat, message_id: mid, text, parse_mode: "HTML", reply_markup: kb, link_preview_options: { is_disabled: true } });
  }
  answer(id, text) { return this.call("answerCallbackQuery", { callback_query_id: id, text: text || undefined }); }
  async react(chat, mid) {
    try { await this.call("setMessageReaction", { chat_id: chat, message_id: mid, reaction: [{ type: "emoji", emoji: "👀" }] }); } catch { /* cosmetic */ }
  }
}
