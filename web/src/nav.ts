import type { ComponentType } from "react";
import type { SvgIconProps } from "@mui/material/SvgIcon";
import { ClientsIcon, HomeIcon, WorkListIcon } from "./components/Icons";

export interface Destination {
  to: string;
  label: string;
  Icon: ComponentType<SvgIconProps>;
  end: boolean;
}

export const destinations: readonly Destination[] = [
  { to: "/", label: "Home", Icon: HomeIcon, end: true },
  { to: "/work", label: "Work list", Icon: WorkListIcon, end: false },
  { to: "/clients", label: "Clients", Icon: ClientsIcon, end: false },
];
