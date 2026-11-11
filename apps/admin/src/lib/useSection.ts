/** Load a config section into an editable draft; save explicitly with PUT. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, get, put, type ValidationIssue } from "./api";
import type { SectionMap, SectionName } from "./types";
import { useToast } from "../components/Toast";

/** Structural equality for JSON-ish values (drafts vs saved). */
export function jsonEqual(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** Issues whose location starts with `path` (e.g. ["providers", 0]). */
export function issuesAt(issues: ValidationIssue[], ...path: (string | number)[]): string[] {
  return issues
    .filter((i) => path.every((p, k) => String(i.loc[k]) === String(p)))
    .map((i) => i.msg);
}

export function useSection<K extends SectionName>(name: K) {
  type T = SectionMap[K];
  const qc = useQueryClient();
  const toast = useToast();
  const query = useQuery({ queryKey: ["config", name], queryFn: () => get<T>(`/api/config/${name}`) });
  const [draft, setDraft] = useState<T | null>(null);
  const [issues, setIssues] = useState<ValidationIssue[]>([]);

  // Take the server copy whenever it changes and we have no unsaved edits.
  useEffect(() => {
    if (query.data && draft === null) setDraft(query.data);
  }, [query.data, draft]);

  const dirty = useMemo(
    () => draft !== null && query.data !== undefined && !jsonEqual(draft, query.data),
    [draft, query.data],
  );

  const save = useMutation({
    mutationFn: (body: T) => put<T>(`/api/config/${name}`, body),
    onSuccess: (saved) => {
      qc.setQueryData(["config", name], saved);
      setDraft(saved);
      setIssues([]);
      toast.show("Saved");
      if (name === "voice") void qc.invalidateQueries({ queryKey: ["voice"] });
      if (name === "providers") void qc.invalidateQueries({ queryKey: ["overview"] });
    },
    onError: (e) => {
      if (e instanceof ApiError && e.status === 422 && e.issues.length) setIssues(e.issues);
      else setIssues([{ loc: [], msg: e.message }]);
    },
  });

  const set = useCallback(<F extends keyof T>(field: F, value: T[F]) => {
    setDraft((d) => (d ? { ...d, [field]: value } : d));
  }, []);

  return {
    query,
    draft,
    setDraft: setDraft as (fn: T | ((d: T | null) => T | null)) => void,
    set,
    dirty,
    issues,
    /** Messages for one field (top-level). */
    fieldIssues: (field: keyof T & string) => issuesAt(issues, field),
    save: () => draft && save.mutate(draft),
    saving: save.isPending,
    reset: () => {
      setDraft(query.data ?? null);
      setIssues([]);
    },
  };
}
