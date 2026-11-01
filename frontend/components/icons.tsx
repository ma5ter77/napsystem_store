// Line icons (1.6 px stroke, currentColor), decorative unless labelled by the control around them.
const P = { fill: "none", stroke: "currentColor", strokeWidth: 1.6, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };

export const BagIcon = () => (
  <svg viewBox="0 0 24 24" aria-hidden="true" {...P}><path d="M5 8h14l-1 12H6L5 8Z" /><path d="M9 8V6.5a3 3 0 0 1 6 0V8" /></svg>
);
export const PersonIcon = () => (
  <svg viewBox="0 0 24 24" aria-hidden="true" {...P}><circle cx="12" cy="8.5" r="3.5" /><path d="M5 20c1.2-3.6 3.8-5.5 7-5.5s5.8 1.9 7 5.5" /></svg>
);
export const MenuIcon = ({ open }: { open?: boolean }) => (
  <svg viewBox="0 0 24 24" aria-hidden="true" {...P}>{open ? <><path d="M6 6l12 12" /><path d="M18 6 6 18" /></> : <><path d="M4 8h16" /><path d="M4 16h16" /></>}</svg>
);
export const CheckIcon = () => (
  <svg viewBox="0 0 12 12" aria-hidden="true" width="12" height="12" {...P} strokeWidth={2}><path d="M2.5 6.2 5 8.6l4.5-5" /></svg>
);
