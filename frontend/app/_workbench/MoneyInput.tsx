"use client";

import { type InputHTMLAttributes, useLayoutEffect, useRef, useState } from "react";

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "type"> & {
  value: number;
  onChange: (value: number) => void;
  /** Save once on blur instead of on every keystroke (for fields that send a request per change). */
  commitOnBlur?: boolean;
  allowNegative?: boolean;
};

const format = (n: number) => (n ? new Intl.NumberFormat("ko-KR").format(n) : "");

/** Won amounts typed with thousands separators ("1,234,567"); empty instead of a leading 0. */
export default function MoneyInput({ value, onChange, commitOnBlur, allowNegative, onBlur, placeholder = "0", ...rest }: Props) {
  const ref = useRef<HTMLInputElement>(null);
  const [text, setText] = useState(format(value));
  const [focused, setFocused] = useState(false);
  const caret = useRef<number | null>(null);

  // Follow outside changes (another field, a reload) unless the person is typing here.
  if (!focused && text !== format(value)) setText(format(value));

  useLayoutEffect(() => {
    // Keep the caret after the same digit once commas are inserted or removed.
    if (caret.current === null || !ref.current) return;
    let digits = caret.current, position = 0;
    while (position < text.length && digits > 0) {
      if (/\d/.test(text[position])) digits -= 1;
      position += 1;
    }
    ref.current.setSelectionRange(position, position);
    caret.current = null;
  }, [text]);

  const parse = (raw: string) => {
    const negative = allowNegative && raw.trim().startsWith("-");
    const digits = raw.replace(/\D/g, "").replace(/^0+(?=\d)/, "");
    const n = Number(digits || 0) * (negative ? -1 : 1);
    return { n, shown: (negative ? "-" : "") + format(Math.abs(n)) };
  };

  return <input
    {...rest}
    ref={ref}
    type="text"
    inputMode="numeric"
    autoComplete="off"
    placeholder={placeholder}
    value={text}
    onFocus={() => setFocused(true)}
    onChange={e => {
      const before = e.target.value.slice(0, e.target.selectionStart ?? e.target.value.length);
      caret.current = before.replace(/\D/g, "").replace(/^0+/, "").length;
      const { n, shown } = parse(e.target.value);
      setText(shown);
      if (!commitOnBlur) onChange(n);
    }}
    onBlur={e => {
      setFocused(false);
      const { n } = parse(text);
      if (commitOnBlur && n !== value) onChange(n);
      onBlur?.(e);
    }}
  />;
}
