"use client";

import { EMPLOYEES, byEmail } from "@/lib/directory";
import { SCENARIOS, type Scenario } from "@/lib/scenarios";
import { Badge, SectionLabel } from "./ui";

export function RequesterPanel({
  email,
  onEmail,
  onScenario,
  disabled,
}: {
  email: string | null;
  onEmail: (email: string | null) => void;
  onScenario: (scenario: Scenario) => void;
  disabled: boolean;
}) {
  const employee = email ? byEmail(email) : undefined;
  const departments = [...new Set(EMPLOYEES.map((person) => person.department))];

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
          value={email ?? ""}
          disabled={disabled}
          onChange={(event) => onEmail(event.target.value || null)}
          className="h-9 w-full rounded-[6px] border border-line-strong bg-background px-2 text-sm transition-colors hover:border-ink-2 disabled:cursor-not-allowed disabled:opacity-60"
        >
          <option value="">Not identified (no email)</option>
          {departments.map((department) => (
            <optgroup key={department} label={department}>
              {EMPLOYEES.filter((person) => person.department === department).map((person) => (
                <option key={person.email} value={person.email}>
                  {person.name}
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
              <div className="flex justify-between gap-3 py-1.5">
                <dt className="text-ink-2">Title</dt>
                <dd className="text-right font-medium">{employee.title}</dd>
              </div>
              <div className="flex justify-between gap-3 py-1.5">
                <dt className="text-ink-2">Department</dt>
                <dd className="text-right font-medium">{employee.department}</dd>
              </div>
              <div className="flex justify-between gap-3 py-1.5">
                <dt className="text-ink-2">Location</dt>
                <dd className="text-right font-medium">{employee.location}</dd>
              </div>
              {employee.vpnClient && (
                <div className="flex justify-between gap-3 py-1.5">
                  <dt className="text-ink-2">VPN client</dt>
                  <dd className="text-right font-mono text-xs">{employee.vpnClient}</dd>
                </div>
              )}
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
          {SCENARIOS.map((scenario) => (
            <li key={scenario.title}>
              <button
                type="button"
                disabled={disabled}
                onClick={() => onScenario(scenario)}
                className="w-full rounded-[6px] px-2 py-2 text-left transition-colors hover:bg-elevated active:bg-line disabled:cursor-not-allowed disabled:opacity-50"
              >
                <span className="block text-sm font-medium">{scenario.title}</span>
                <span className="block text-xs text-ink-2">{scenario.hint}</span>
              </button>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
