// Small helpers shared by the Worker modules.

export function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/** Strict ASCII integer (rejects "²", "1e3", " 7"). */
export function toInt(v) {
  return /^-?\d+$/.test(v) ? Number(v) : null;
}

/** Kyiv local date and time for a Date, DST-aware (EEST/EET). */
export function kyivNow(date = new Date()) {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-GB", {
      timeZone: "Europe/Kyiv", year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", weekday: "short", hourCycle: "h23",
    }).formatToParts(date).map((p) => [p.type, p.value]),
  );
  const dows = { Mon: 0, Tue: 1, Wed: 2, Thu: 3, Fri: 4, Sat: 5, Sun: 6 };
  return { day: `${parts.year}-${parts.month}-${parts.day}`, hhmm: `${parts.hour}:${parts.minute}`, dow: dows[parts.weekday] };
}

export function addDays(day, n) {
  const d = new Date(`${day}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

export function weekday(day) {
  return (new Date(`${day}T12:00:00Z`).getUTCDay() + 6) % 7; // Mon = 0
}

export function plusMinutes(hhmm, m) {
  const [h, mm] = hhmm.split(":").map(Number);
  const t = h * 60 + mm + m;
  return `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(t % 60).padStart(2, "0")}`;
}
