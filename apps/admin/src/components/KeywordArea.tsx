import { useEffect, useState } from "react";
import { jsonEqual } from "../lib/useSection";
import { linesToList } from "../lib/reorder";
import { Field, Textarea } from "./ui";

/** Textarea editing a string list, one item per line. Keeps raw text so blank lines can be typed. */
export function ListArea({
  label,
  hint,
  value,
  onChange,
  rows = 8,
  errors,
}: {
  label: string;
  hint?: string;
  value: string[];
  onChange: (v: string[]) => void;
  rows?: number;
  errors?: string[];
}) {
  const [text, setText] = useState(value.join("\n"));
  // Follow external changes (revert, save) without fighting the user's typing.
  useEffect(() => {
    if (!jsonEqual(linesToList(text), value)) setText(value.join("\n"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);
  return (
    <Field label={label} hint={hint} errors={errors}>
      {(id) => (
        <Textarea
          id={id}
          rows={rows}
          value={text}
          spellCheck={false}
          onChange={(e) => {
            setText(e.target.value);
            onChange(linesToList(e.target.value));
          }}
        />
      )}
    </Field>
  );
}
