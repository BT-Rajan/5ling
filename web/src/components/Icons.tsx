import type { SvgIconProps } from "@mui/material/SvgIcon";
import SvgIcon from "@mui/material/SvgIcon";

// Simple 24px outline icons, drawn here so the app ships no icon package.
function Outline(props: SvgIconProps & { d: string }) {
  const { d, ...rest } = props;
  return (
    <SvgIcon {...rest}>
      <path d={d} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </SvgIcon>
  );
}

export const HomeIcon = (p: SvgIconProps) => <Outline {...p} d="M3 11.5 12 4l9 7.5M5.5 10v9.5h13V10M10 19.5v-5h4v5" />;
export const WorkListIcon = (p: SvgIconProps) => (
  <Outline {...p} d="M10 6.5h10M10 12h10M10 17.5h10M3.8 6.6l1.1 1.1 2-2.2M3.8 12.1l1.1 1.1 2-2.2M3.8 17.6l1.1 1.1 2-2.2" />
);
export const ClientsIcon = (p: SvgIconProps) => (
  <Outline
    {...p}
    d="M9 11.2a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4ZM2.8 19.5c0-3.2 2.8-5.2 6.2-5.2s6.2 2 6.2 5.2M16.5 11a2.6 2.6 0 1 0 0-5.2M18 14.5c2 .5 3.2 2 3.2 4.5"
  />
);

export function LogoMark({ size = 32 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" role="img" aria-label="Ledgerline">
      <rect width="64" height="64" rx="14" fill="#1F6B67" />
      <rect x="14" y="16" width="36" height="6" rx="3" fill="#fff" />
      <rect x="14" y="29" width="28" height="6" rx="3" fill="#fff" />
      <rect x="14" y="42" width="18" height="6" rx="3" fill="#E3B341" />
    </svg>
  );
}
