import type { ComponentType } from "react";
import type { SvgIconProps } from "@mui/material/SvgIcon";
import type { Role } from "./auth/AuthProvider";
import { ClientsIcon, HomeIcon, TeamIcon, WorkListIcon } from "./components/Icons";

export interface Destination {
  to: string;
  label: string;
  Icon: ComponentType<SvgIconProps>;
  end: boolean;
  /** Who sees this in the menu. The server still checks every request; this only tidies the menu. */
  roles: readonly Role[];
}

export const destinations: readonly Destination[] = [
  { to: "/", label: "Home", Icon: HomeIcon, end: true, roles: ["administrator", "staff", "consultant"] },
  { to: "/work", label: "Work list", Icon: WorkListIcon, end: false, roles: ["administrator", "staff"] },
  { to: "/clients", label: "Clients", Icon: ClientsIcon, end: false, roles: ["administrator", "staff"] },
  { to: "/team", label: "Team", Icon: TeamIcon, end: false, roles: ["administrator"] },
];

const OTHER_TITLES: readonly (readonly [string, string])[] = [["/account/password", "Change password"]];

export function pageTitle(pathname: string): string {
  const extra = OTHER_TITLES.find(([path]) => pathname === path);
  if (extra) return extra[1];
  const dest = destinations.find((d) => (d.end ? pathname === d.to : pathname.startsWith(d.to)));
  return dest?.label ?? "Not found";
}
