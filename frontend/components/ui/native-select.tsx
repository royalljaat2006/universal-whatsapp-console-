import * as React from "react";

import { cn } from "@/lib/utils";

/** Styled native <select>: accessible and keyboard-friendly without extra JS. */
export function NativeSelect({ className, ...props }: React.ComponentProps<"select">) {
  return (
    <select
      data-slot="native-select"
      className={cn(
        "bg-background focus-visible:ring-ring/50 h-8 rounded-md border px-2 text-sm outline-none focus-visible:ring-3 disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}

export function Textarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return (
    <textarea
      data-slot="textarea"
      className={cn(
        "bg-background focus-visible:ring-ring/50 min-h-[44px] w-full rounded-md border px-3 py-2 text-sm outline-none focus-visible:ring-3 disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}
