// One outline icon set: 16px grid, 1.6 stroke, round caps. Decorative by default (aria-hidden).
import type { ReactNode, SVGProps } from 'react'

function Icon({ children, ...p }: SVGProps<SVGSVGElement> & { children: ReactNode }) {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true" {...p}>{children}</svg>
  )
}

export const IconOverview = () => <Icon><path d="M2.5 13.5V8m3.7 5.5V3m3.6 10.5V7m3.7 6.5V5" /></Icon>
export const IconRisk = () => <Icon><circle cx="8" cy="8" r="5.8" /><circle cx="8" cy="8" r="2.4" /></Icon>
export const IconUpload = () => <Icon><path d="M8 10.5V2.8M4.8 5.8 8 2.6l3.2 3.2M2.8 13.2h10.4" /></Icon>
export const IconRuns = () => <Icon><path d="M2.6 4h10.8M2.6 8h7.4M2.6 12h4.6" /><circle cx="12.6" cy="11.4" r="1.6" /></Icon>
export const IconTeam = () => <Icon><circle cx="6" cy="5.8" r="2.4" /><path d="M1.8 13.2c.6-2.3 2.2-3.5 4.2-3.5s3.6 1.2 4.2 3.5M10.9 3.6a2.2 2.2 0 0 1 0 4.4M12.4 9.9c1 .6 1.6 1.7 1.9 3.3" /></Icon>
export const IconOrg = () => <Icon><path d="M2.6 13.4V3.6l5-1.6v11.4M7.6 5.4l5.8 1.8v6.2M1.6 13.4h12.8M4.6 6h1M4.6 8.6h1M4.6 11.2h1M10 9h1.4M10 11.4h1.4" /></Icon>
export const IconAudit = () => <Icon><path d="M4 2.4h5.6L12.4 5v8.6H4z" /><path d="M9.4 2.4V5.2h3M6.2 8h4M6.2 10.6h4" /></Icon>
export const IconTable = () => <Icon><rect x="2.4" y="2.6" width="11.2" height="10.8" rx="1.6" /><path d="M2.4 6.2h11.2M2.4 9.8h11.2M6.4 6.2v7.2" /></Icon>
export const IconSearch = () => <Icon><circle cx="7" cy="7" r="4.4" /><path d="m10.4 10.4 3.2 3.2" /></Icon>
export const IconBack = () => <Icon><path d="M9.8 3.4 5.2 8l4.6 4.6" /></Icon>
export const IconArrow = () => <Icon><path d="M3 8h10M9 4l4 4-4 4" /></Icon>
export const IconCustomer = () => <Icon><circle cx="8" cy="5.6" r="2.8" /><path d="M2.8 13.6c.8-2.6 2.8-4 5.2-4s4.4 1.4 5.2 4" /></Icon>
export const IconCheck = () => <Icon><path d="m3.4 8.4 3 3 6.2-6.6" /></Icon>
export const IconPulse = () => <Icon><path d="M1.8 8.4h3l1.8-4.2 2.8 7.6 1.8-3.4h3" /></Icon>
export const IconFile = () => <Icon><path d="M4 1.8h5.4L12.6 5v9.2H4z" /><path d="M9.2 1.8V5.2h3.4" /></Icon>
export const IconKey = () => <Icon><circle cx="5.4" cy="10.6" r="2.8" /><path d="m7.4 8.6 6-6M11.2 4.8l1.6 1.6" /></Icon>

// Brand mark: a cobalt tile with a line that dips and recovers (a customer won back).
export function Mark() {
  return (
    <svg viewBox="0 0 28 28" aria-hidden="true">
      <rect x="1" y="1" width="26" height="26" rx="8" fill="var(--accent)" />
      <path d="M7.5 11c2.2 0 3 7 5.6 7 2.8 0 2.8-8.6 7.4-8.6" stroke="var(--on-accent)" strokeWidth="2.3" fill="none" strokeLinecap="round" />
      <circle cx="20.5" cy="9.4" r="2" fill="var(--on-accent)" />
    </svg>
  )
}
