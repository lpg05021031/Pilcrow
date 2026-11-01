import { useId, useState } from 'react';
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

/**
 * A themed replacement for <select>: a button that opens a menu of choices (menuitemradio), the
 * chosen one focused. Named by `label`, or inside a ui/Field by the row's label and its own value.
 */
export function Select<T extends string>({ value, options, onChange, className = '', width, title, placeholder, id, label }: { value: T; options: SelectOption<T>[]; onChange: (v: T) => void; className?: string; width?: number; title?: string; placeholder?: string; id?: string; label?: string }) {
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  const menuId = useId();
  const current = options.find((o) => o.value === value);
  const items: MenuItem[] = options.map((o) => ({ label: o.label, hint: o.hint, icon: o.icon, mono: o.mono, checked: o.value === value, disabled: o.disabled, onSelect: () => onChange(o.value) }));
  const Icon = current?.icon;
  return (
    <>
      <button ref={setAnchor} id={id} type="button" className={`select-btn ${className}`} aria-haspopup="menu" aria-expanded={open} aria-controls={open ? menuId : undefined} aria-label={label} aria-labelledby={id && !label ? `${id}-label ${id}` : undefined} title={title} onClick={() => setOpen((o) => !o)}>
        {Icon && <Icon size={14} className="select-ico" />}
        <span className={`ellipsis grow${current ? '' : ' faint'}`}>{current?.label || placeholder || 'Choose…'}</span>
        <ChevronsUpDown size={13} className="select-chev" />
      </button>
      <Popover anchor={anchor} open={open} onClose={() => setOpen(false)} placement="bottom-start" width={width || 'anchor'}>
        <Menu id={menuId} label={label} items={items} onClose={() => setOpen(false)} />
      </Popover>
    </>
  );
}
