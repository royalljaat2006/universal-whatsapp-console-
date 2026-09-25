"use client";

import { useRef, useState } from "react";

import { ApiErrorNotice } from "@/components/status/api-error-notice";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { ApiError } from "@/lib/api/errors";
import { formatBytes, toApiError } from "@/lib/format";
import { inboxService } from "@/services/inbox";
import { mediaService } from "@/services/media";
import type { InboxConversation, InboxMessage, MediaAsset } from "@/types/api";

import { useSend } from "./use-send";

/**
 * Types accepted by the WhatsApp Cloud API for outbound media (verified,
 * docs/integrations/meta/api-assumptions.md). This list only filters the file
 * picker; the backend re-validates type, size and file signature.
 */
export const OUTBOUND_ACCEPT = [
  "image/jpeg",
  "image/png",
  "video/mp4",
  "video/3gpp",
  "audio/aac",
  "audio/amr",
  "audio/mpeg",
  "audio/mp4",
  "audio/ogg",
  "text/plain",
  "application/pdf",
  "application/msword",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.ms-excel",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "application/vnd.ms-powerpoint",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
].join(",");

export function MediaComposer({
  conversation,
  onQueued,
}: {
  conversation: InboxConversation;
  onQueued: (message: InboxMessage) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [asset, setAsset] = useState<MediaAsset | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<ApiError | null>(null);
  const [caption, setCaption] = useState("");
  const { send, sending, error } = useSend(onQueued);

  async function pick(file: File | undefined) {
    if (!file) return;
    setUploading(true);
    setUploadError(null);
    setAsset(null);
    try {
      setAsset(await mediaService.upload(file));
    } catch (cause) {
      setUploadError(toApiError(cause));
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!asset) return;
    const ok = await send((key) => inboxService.sendMedia(conversation.id, asset.id, caption.trim(), key));
    if (ok) {
      setAsset(null);
      setCaption("");
    }
  }

  const captionAllowed = asset && asset.kind !== "audio";

  return (
    <form onSubmit={submit} className="flex flex-col gap-2">
      {uploadError ? <ApiErrorNotice error={uploadError} /> : null}
      {error ? <ApiErrorNotice error={error} /> : null}
      <div className="flex flex-wrap items-center gap-2">
        <input
          ref={inputRef}
          type="file"
          aria-label="Attach file"
          accept={OUTBOUND_ACCEPT}
          className="text-xs"
          onChange={(e) => void pick(e.target.files?.[0])}
          disabled={uploading || sending}
        />
        {uploading ? <span className="text-muted-foreground text-xs">Uploading…</span> : null}
        {asset ? (
          <span className="text-xs">
            {asset.original_filename} · {asset.kind} · {formatBytes(asset.file_size)}
          </span>
        ) : null}
      </div>
      <div className="flex gap-2">
        <Input
          aria-label="Caption"
          placeholder={captionAllowed ? "Caption (optional)" : "Audio messages have no caption"}
          value={caption}
          maxLength={1024}
          disabled={!captionAllowed}
          onChange={(e) => setCaption(e.target.value)}
        />
        <Button type="submit" disabled={!asset || sending || uploading}>
          {sending ? "Sending…" : "Send file"}
        </Button>
      </div>
      <p className="text-muted-foreground text-[11px]">
        Images JPEG/PNG ≤ 5 MB · video MP4/3GP ≤ 16 MB · audio ≤ 16 MB · documents ≤ 100 MB.
      </p>
    </form>
  );
}
