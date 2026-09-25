"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { useMediaUrl } from "@/hooks/use-media-url";
import { formatBytes } from "@/lib/format";
import type { Attachment } from "@/types/api";

const AUTO_LOAD = new Set(["image", "sticker"]);

/**
 * Renders stored media from the backend content endpoint. Pending/failed
 * states are shown as they are stored — inbound media is downloaded from Meta
 * asynchronously, so "pending" is a real state, not a placeholder.
 */
export function MessageAttachment({ attachment }: { attachment: Attachment }) {
  const [requested, setRequested] = useState(AUTO_LOAD.has(attachment.kind));
  const media = useMediaUrl(attachment.content_url, requested && attachment.kind !== "document");
  const label = attachment.filename || `${attachment.kind} (${attachment.mime_type || "unknown type"})`;

  if (attachment.storage_status === "pending") {
    return <p className="text-xs italic opacity-80">{attachment.kind} — downloading from WhatsApp…</p>;
  }
  if (attachment.storage_status === "failed" || !attachment.content_url) {
    return (
      <p className="text-xs italic opacity-80">
        {attachment.kind} unavailable{attachment.last_error ? ` (${attachment.last_error})` : ""}
      </p>
    );
  }

  if (attachment.kind === "document") return <DocumentLink attachment={attachment} label={label} />;

  if (!requested) {
    return (
      <Button variant="outline" size="xs" onClick={() => setRequested(true)}>
        Load {attachment.kind} · {formatBytes(attachment.file_size)}
      </Button>
    );
  }
  if (media.failed) return <p className="text-xs italic opacity-80">Could not load {attachment.kind}.</p>;
  if (!media.url) return <p className="text-xs italic opacity-80">Loading {attachment.kind}…</p>;

  switch (attachment.kind) {
    case "image":
    case "sticker":
      return (
        // eslint-disable-next-line @next/next/no-img-element -- object URL, not optimisable
        <img
          src={media.url}
          alt={attachment.caption || label}
          className={attachment.kind === "sticker" ? "max-h-32" : "max-h-72 rounded-md"}
        />
      );
    case "video":
      return <video src={media.url} controls className="max-h-72 rounded-md" />;
    case "audio":
      return <audio src={media.url} controls />;
    default:
      return <DocumentLink attachment={attachment} label={label} />;
  }
}

function DocumentLink({ attachment, label }: { attachment: Attachment; label: string }) {
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  async function download() {
    if (!attachment.content_url) return;
    setBusy(true);
    setFailed(false);
    try {
      const { apiBlob } = await import("@/lib/api/client");
      const blob = await apiBlob(attachment.content_url);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = attachment.filename || "document";
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="flex items-center gap-2 text-xs">
      <span className="truncate">📄 {label}</span>
      <span className="opacity-70">{formatBytes(attachment.file_size)}</span>
      <Button variant="outline" size="xs" onClick={() => void download()} disabled={busy}>
        {busy ? "…" : failed ? "Retry" : "Download"}
      </Button>
    </span>
  );
}
