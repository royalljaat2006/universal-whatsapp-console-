import { apiUpload } from "@/lib/api/client";
import type { MediaAsset } from "@/types/api";

export const mediaService = {
  upload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return apiUpload<MediaAsset>("/api/v1/media/", form);
  },
};
