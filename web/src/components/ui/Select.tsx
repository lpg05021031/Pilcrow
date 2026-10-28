import { useRef, useState } from 'react';
import { ChevronsUpDown, type LucideIcon } from 'lucide-react';
import { Popover, Menu, type MenuItem } from './Popover';

export interface SelectOption<T extends string> {
  value: T;
  label: string;
  hint?: string;
  icon?: LucideIcon;
  mono?: boolean;
  disabled?: boolean;
}

/** A themed replacement for <select>: a button that opens the app's own menu. */
export function Select<T extends string>({ value, options, onChange, className = '', width, title, placeholder }: { value: T; options: SelectOption<T>[]; onChange: (v: T) => void; className?: string; width?: number; title?: string; placeholder?: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLButtonElement>(null);
  const current = options.find((o) => o.value === value);
  const items: MenuItem[] = options.map((o) => ({ label: o.label, hint: o.hint, icon: o.icon, mono: o.mono, checked: o.value === value, disabled: o.disabled, onSelect: () => onChange(o.value) }));
  const Icon = current?.icon;
  return (
    <>
      <button ref={ref} type="button" className={`select-btn ${className}`} aria-haspopup="listbox" aria-expanded={open} title={title} onClick={() => setOpen((o) => !o)}>
        {Icon && <Icon size={14} className="select-ico" />}
        <span className={`ellipsis grow${current ? '' : ' faint'}`}>{current?.label || placeholder || 'Choose…'}</span>
        <ChevronsUpDown size={13} className="select-chev" />
      </button>
      <Popover anchor={ref.current} open={open} onClose={() => setOpen(false)} placement="bottom-start" width={width || ref.current?.offsetWidth}>
        <Menu items={items} onClose={() => setOpen(false)} />
      </Popover>
    </>
  );
}
