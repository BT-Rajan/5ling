/** Times are stored in UTC and always shown in India time (BRD NFR-AU-01). */
const IST = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata",
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

export function formatIST(iso: string): string {
  const when = new Date(iso);
  return Number.isNaN(when.getTime()) ? iso : `${IST.format(when)} IST`;
}

const DAY = new Intl.DateTimeFormat("en-IN", { weekday: "short", day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });

/** A calendar date ("2026-11-11", no time) as "Wed 11 Nov 2026". Never shifted by time zones. */
export function formatDay(isoDate: string): string {
  const parts = /^(\d{4})-(\d{2})-(\d{2})$/.exec(isoDate);
  if (!parts) return isoDate;
  const found = DAY.formatToParts(new Date(Date.UTC(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3]))));
  const get = (type: Intl.DateTimeFormatPartTypes) => found.find((p) => p.type === type)?.value ?? "";
  return `${get("weekday")} ${get("day")} ${get("month")} ${get("year")}`;
}
