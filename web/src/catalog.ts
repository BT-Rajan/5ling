/** Shapes from the package and filing-calendar endpoints, and plain-words descriptions. */

export type Frequency = "monthly" | "quarterly" | "yearly";
export type PeriodBasis = "financial_year" | "calendar_year";
export type HolidayRule = "earlier_working_day" | "as_is";
export type BillingFrequency = "monthly" | "quarterly" | "yearly" | "one_time";

export interface ChecklistEntry {
  label: string;
  required: boolean;
}

export interface DueDay {
  months_after: number;
  day: number;
}

export interface DueRule extends DueDay {
  overrides: Record<string, DueDay>;
}

export interface Calendar {
  id: number;
  version: number;
  status: "draft" | "published";
  frequency: Frequency;
  period_basis: PeriodBasis;
  due: DueRule;
  lead_days: number | null;
  holiday_rule: HolidayRule;
  notes: string | null;
  published_at: string | null;
}

export interface FilingType {
  id: number;
  code: string;
  name: string;
  description: string | null;
  default_checklist: ChecklistEntry[];
  is_active: boolean;
  current: Calendar | null;
  draft: Calendar | null;
}

export interface FilingDetail extends FilingType {
  versions: Calendar[];
}

export interface VersionFiling {
  filing_id: number;
  code: string;
  name: string;
  checklist: (ChecklistEntry & { id: number })[];
}

export interface PackageVersion {
  id: number;
  version: number;
  status: "draft" | "published";
  fee: string | null;
  billing_frequency: BillingFrequency | null;
  notes: string | null;
  filings: VersionFiling[];
  published_at: string | null;
}

export interface PackageSummary {
  id: number;
  name: string;
  description: string | null;
  is_active: boolean;
  current_version: number | null;
  current_fee: string | null;
  current_billing_frequency: BillingFrequency | null;
  current_filings: number;
  has_draft: boolean;
}

export interface PackageDetail {
  id: number;
  name: string;
  description: string | null;
  is_active: boolean;
  versions: PackageVersion[];
}

export const FREQUENCY_LABEL: Record<Frequency, string> = { monthly: "Monthly", quarterly: "Quarterly", yearly: "Yearly" };
export const BILLING_LABEL: Record<BillingFrequency, string> = {
  monthly: "monthly",
  quarterly: "quarterly",
  yearly: "yearly",
  one_time: "one time",
};
export const BILLING_OPTION: Record<BillingFrequency, string> = {
  monthly: "Monthly",
  quarterly: "Quarterly",
  yearly: "Yearly",
  one_time: "One time",
};
export const BASIS_LABEL: Record<PeriodBasis, string> = {
  financial_year: "Financial year (April to March)",
  calendar_year: "Calendar year (January to December)",
};
export const HOLIDAY_LABEL: Record<HolidayRule, string> = {
  earlier_working_day: "Move to the earlier working day",
  as_is: "Keep the date as it is",
};

/** Period names a calendar may give its own due date: M1 to M12, Q1 to Q4, none for yearly. */
export function periodKeys(frequency: Frequency): string[] {
  if (frequency === "monthly") return Array.from({ length: 12 }, (_, i) => `M${i + 1}`);
  if (frequency === "quarterly") return ["Q1", "Q2", "Q3", "Q4"];
  return [];
}

function ordinal(n: number): string {
  const tail = n % 100 >= 11 && n % 100 <= 13 ? "th" : (["th", "st", "nd", "rd"][n % 10] ?? "th");
  return `${n}${tail}`;
}

export function dueDayText({ months_after, day }: DueDay): string {
  const which = day >= 31 ? "last day" : ordinal(day);
  if (months_after === 0) return `${which} of the period's last month`;
  return `${which} of the ${months_after === 1 ? "month" : `${ordinal(months_after)} month`} after the period`;
}

export function calendarSummary(c: Calendar): string {
  const extra = Object.keys(c.due.overrides).length;
  return `${FREQUENCY_LABEL[c.frequency]} · due ${dueDayText(c.due)}${extra ? ` (${extra} exception${extra > 1 ? "s" : ""})` : ""}`;
}

export function money(fee: string | null): string {
  if (fee === null) return "no fee yet";
  const [whole = "0", fraction = "00"] = fee.split(".");
  const grouped = new Intl.NumberFormat("en-IN").format(Number(whole));
  return `₹${grouped}${fraction === "00" ? "" : `.${fraction}`}`;
}
