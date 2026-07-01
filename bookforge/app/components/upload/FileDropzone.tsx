"use client";

import { useCallback, useRef, useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { Icon } from "@/components/ui/Icon";
import { Spinner } from "@/components/ui/Spinner";
import { ACCEPTED_FILE_EXTENSIONS } from "@/lib/constants";
import {
  countWords,
  detectBinaryContent,
  formatFileSize,
  isAcceptedFile,
  readFileAsText,
  shouldUseMultipartUpload,
} from "@/lib/files";
import { cn } from "@/lib/cn";

export type FileSelection = {
  file: File;
  text: string;
  useMultipart: boolean;
};

type FileDropzoneProps = {
  onSelect: (selection: FileSelection | null) => void;
  disabled?: boolean;
};

export function FileDropzone({ onSelect, disabled }: FileDropzoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [reading, setReading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selection, setSelection] = useState<FileSelection | null>(null);

  const processFile = useCallback(
    async (file: File) => {
      setError(null);
      if (!isAcceptedFile(file)) {
        setError(`Accepted formats: ${ACCEPTED_FILE_EXTENSIONS.join(", ")}`);
        onSelect(null);
        return;
      }
      if (file.size === 0) {
        setError("File is empty");
        onSelect(null);
        return;
      }

      const useMultipart = shouldUseMultipartUpload(file);
      if (useMultipart) {
        const sel = { file, text: "", useMultipart: true };
        setSelection(sel);
        onSelect(sel);
        return;
      }

      setReading(true);
      try {
        const text = await readFileAsText(file);
        if (!text.trim()) {
          setError("File has no readable text content");
          onSelect(null);
          return;
        }
        if (detectBinaryContent(text)) {
          setError(
            "File appears to be binary. Use a plain text or Markdown file."
          );
          onSelect(null);
          return;
        }
        const sel = { file, text, useMultipart: false };
        setSelection(sel);
        onSelect(sel);
      } catch {
        setError("Failed to read file");
        onSelect(null);
      } finally {
        setReading(false);
      }
    },
    [onSelect]
  );

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      if (disabled || reading) return;
      const file = e.dataTransfer.files[0];
      if (file) void processFile(file);
    },
    [disabled, reading, processFile]
  );

  const onInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) void processFile(file);
  };

  return (
    <div className="space-y-3">
      <div
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            inputRef.current?.click();
          }
        }}
        onDragOver={(e) => {
          e.preventDefault();
          if (!disabled) setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        onClick={() => !disabled && !reading && inputRef.current?.click()}
        className={cn(
          "flex h-64 cursor-pointer flex-col items-center justify-center gap-4 rounded-xl border-2 border-dashed transition-colors",
          dragOver
            ? "border-primary bg-primary/5"
            : "border-outline bg-surface-container-lowest hover:bg-surface-container",
          (disabled || reading) && "pointer-events-none opacity-60"
        )}
        aria-label="Upload source file"
      >
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPTED_FILE_EXTENSIONS.join(",")}
          onChange={onInputChange}
          className="sr-only"
          disabled={disabled || reading}
        />
        {reading ? (
          <div className="flex flex-col items-center gap-2">
            <Spinner />
            <p className="font-body text-body-sm text-secondary">
              Reading file…
            </p>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-2">
            <Icon
              name="upload_file"
              className="text-secondary transition-transform group-hover:scale-110"
              size={40}
            />
            <div className="text-center">
              <p className="font-display text-headline-sm text-on-surface">
                Drop files here
              </p>
              <p className="mt-1 font-body text-body-sm text-secondary">
                TXT or Markdown — up to server limit
              </p>
            </div>
          </div>
        )}
      </div>

      {selection && (
        <div className="rounded-lg border border-tertiary/30 bg-tertiary/5 px-4 py-3 font-body text-body-sm">
          <p className="font-medium text-tertiary">{selection.file.name}</p>
          <p className="mt-1 text-secondary">
            {formatFileSize(selection.file.size)}
            {selection.text
              ? ` · ${selection.text.length.toLocaleString()} chars · ${countWords(selection.text).toLocaleString()} words`
              : " · large file — will upload directly"}
          </p>
        </div>
      )}

      {error && <Alert variant="error">{error}</Alert>}
    </div>
  );
}
