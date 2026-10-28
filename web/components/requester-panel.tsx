"use client";

import { EMPLOYEES, byEmail } from "@/lib/directory";
import { SCENARIOS, type Scenario } from "@/lib/scenarios";
import { Badge, SectionLabel } from "./ui";

const NONE = "";

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between gap-3 py-1.5">
      <dt className="text-ink-2">{k}</dt>
      <dd className="text-right font-medium">{v}</dd>
    </div>
  );
}

export function RequesterPanel({
  email,
  onEmail,
  onScenario,
  disabled,
}: {
  email: string | null;
  onEmail: (email: string | null) => void;
  onScenario: (s: Scenario) => void;
  disabled: boolean;
}) {
  const employee = email ? byEmail(email) : undefined;
  const departments = [...new Set(EMPLOYEES.map((e) => e.department))];

  return (
    <div className="flex flex-col gap-8 p-5">
      <section aria-labelledby="requester-heading" className="space-y-3">
        <SectionLabel>
          <span id="requester-heading">Requester</span>
        </SectionLabel>
        <label htmlFor="requester" className="sr-only">
          Choose who is asking
        </label>
        <select
          id="requester"
          value={email ?? NONE}
          disabled={disabled}
          onChange={(e) => onEmail(e.target.value || null)}
          className="h-9 w-full rounded-[6px] border border-line-strong bg-background px-2 text-sm transition-colors hover:border-ink-2 disabled:cursor-not-allowed disabled:opacity-60"
        >
          <option value={NONE}>Not identified (no email)</option>
          {departments.map((dept) => (
            <optgroup key={dept} label={dept}>
              {EMPLOYEES.filter((e) => e.department === dept).map((e) => (
                <option key={e.email} value={e.email}>
                  {e.name}
                </option>
              ))}
            </optgroup>
          ))}
        </select>

        {employee ? (
          <div className="rounded-[8px] border border-line bg-surface px-3 py-2">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="font-medium">{employee.name}</span>
              {employee.vip && <Badge tone="accent">VIP</Badge>}
              {employee.contractor && <Badge>Contractor</Badge>}
            </div>
            <p className="break-all font-mono text-xs text-ink-2">{employee.email}</p>
            <dl className="mt-2 divide-y divide-line border-t border-line text-sm">
              <Row k="Title" v={employee.title} />
              <Row k="Department" v={employee.department} />
              <Row k="Location" v={employee.location} />
              {employee.vpnClient && <Row k="VPN client" v={employee.vpnClient} />}
            </dl>
          </div>
        ) : (
          <p className="text-sm text-ink-2">
            Without an email the assistant can&apos;t check the directory, so it will ask who you are.
          </p>
        )}
      </section>

      <section aria-labelledby="scenarios-heading" className="space-y-1">
        <SectionLabel>
          <span id="scenarios-heading">Try a scenario</span>
        </SectionLabel>
        <ul className="-mx-2 pt-1">
          {SCENARIOS.map((s) => (
            <li key={s.title}>
              <button
                type="button"
                disabled={disabled}
                onClick={() => onScenario(s)}
                className="w-full rounded-[6px] px-2 py-2 text-left transition-colors hover:bg-elevated active:bg-line disabled:cursor-not-allowed disabled:opacity-50"
              >
                <span className="block text-sm font-medium">{s.title}</span>
                <span className="block text-xs text-ink-2">{s.hint}</span>
              </button>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
