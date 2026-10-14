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
