"use client";

import { PlugIcon } from "lucide-react";
import { type ComponentProps, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { LoadingButton } from "@/components/ui/spinner";
import { useToast } from "@/components/ui/toast";
import { toApiError } from "@/lib/format";
import { whatsappService } from "@/services/whatsapp";

// Minimal surface of the Facebook JS SDK we actually call (verified 2026-09-26
// against developers.facebook.com/documentation/business-messaging/whatsapp/
// embedded-signup/implementation — see docs/integrations/meta/
// api-assumptions.md#embedded-signup). Not Meta's full SDK typings.
interface FacebookLoginResponse {
  authResponse?: { code?: string } | null;
}
interface FacebookSdk {
  init(options: { appId: string; version: string; xfbml?: boolean }): void;
  login(
    callback: (response: FacebookLoginResponse) => void,
    options: {
      config_id: string;
      response_type: "code";
      override_default_response_type: true;
      extras: { setup: Record<string, unknown> };
    },
  ): void;
}
declare global {
  interface Window {
    FB?: FacebookSdk;
    fbAsyncInit?: () => void;
  }
}

let sdkPromise: Promise<FacebookSdk> | null = null;

function loadFacebookSdk(): Promise<FacebookSdk> {
  sdkPromise ??= new Promise((resolve, reject) => {
    if (window.FB) {
      resolve(window.FB);
      return;
    }
    window.fbAsyncInit = () => resolve(window.FB!);
    const script = document.createElement("script");
    script.src = "https://connect.facebook.net/en_US/sdk.js";
    script.async = true;
    script.defer = true;
    script.crossOrigin = "anonymous";
    script.onerror = () => reject(new Error("Could not load the Facebook SDK."));
    document.body.appendChild(script);
  });
  return sdkPromise;
}

interface EmbeddedSignupEventData {
  type?: string;
  event?: "FINISH" | "CANCEL";
  data?: { phone_number_id?: string; waba_id?: string; current_step?: string };
}

/**
 * Launches Meta's Embedded Signup (v4) and completes the connection once both
 * halves of the result have arrived: the message event (waba_id,
 * phone_number_id) and the FB.login callback (the exchangeable code) can
 * arrive in either order and must both be present before we call our backend.
 */
export function EmbeddedSignupButton({
  businessId,
  onConnected,
  variant,
  size,
}: {
  businessId: string;
  onConnected: () => void;
  variant?: ComponentProps<typeof Button>["variant"];
  size?: ComponentProps<typeof Button>["size"];
}) {
  const toast = useToast();
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [connecting, setConnecting] = useState(false);
  const pending = useRef<{ waba_id?: string; phone_number_id?: string; code?: string }>({});
  const submitted = useRef(false);

  useEffect(() => {
    whatsappService
      .embeddedSignupConfig()
      .then((config) => setConfigured(config.configured))
      .catch(() => setConfigured(false));
  }, []);

  useEffect(() => {
    function onMessage(event: MessageEvent) {
      if (!event.origin.endsWith("facebook.com")) return;
      let payload: EmbeddedSignupEventData;
      try {
        payload = JSON.parse(event.data);
      } catch {
        return;
      }
      if (payload.type !== "WA_EMBEDDED_SIGNUP") return;
      if (payload.event === "CANCEL") {
        setConnecting(false);
        toast.info("Connection cancelled", "You can try connecting again any time.");
        return;
      }
      if (payload.event === "FINISH" && payload.data) {
        pending.current.waba_id = payload.data.waba_id;
        pending.current.phone_number_id = payload.data.phone_number_id;
        void tryComplete();
      }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- tryComplete closes over refs only
  }, []);

  async function tryComplete() {
    const { waba_id, phone_number_id, code } = pending.current;
    if (!waba_id || !phone_number_id || !code || submitted.current) return;
    submitted.current = true;
    try {
      await whatsappService.completeEmbeddedSignup({ business_id: businessId, waba_id, phone_number_id, code });
      toast.success("WhatsApp connecting", "Verifying and syncing numbers — this finishes in the background.");
      onConnected();
    } catch (cause) {
      toast.error("Couldn't complete the connection", toApiError(cause).message);
    } finally {
      setConnecting(false);
      pending.current = {};
      submitted.current = false;
    }
  }

  async function launch() {
    setConnecting(true);
    pending.current = {};
    submitted.current = false;
    try {
      const [config, FB] = await Promise.all([
        whatsappService.embeddedSignupConfig(),
        loadFacebookSdk(),
      ]);
      if (!config.configured) {
        toast.error(
          "WhatsApp connection isn't configured yet",
          "Ask an administrator to set META_APP_ID and META_EMBEDDED_SIGNUP_CONFIG_ID.",
        );
        setConnecting(false);
        return;
      }
      FB.init({ appId: config.app_id, version: config.graph_api_version, xfbml: true });
      FB.login(
        (response) => {
          const code = response.authResponse?.code;
          if (!code) {
            // Dialog closed without completing — not an error, just stop.
            setConnecting(false);
            return;
          }
          pending.current.code = code;
          void tryComplete();
        },
        {
          config_id: config.config_id,
          response_type: "code",
          override_default_response_type: true,
          extras: { setup: {} },
        },
      );
    } catch (cause) {
      toast.error("Couldn't start WhatsApp connection", toApiError(cause).message);
      setConnecting(false);
    }
  }

  return (
    <LoadingButton
      variant={variant ?? "default"}
      size={size ?? "sm"}
      loading={connecting}
      disabled={configured === false}
      onClick={() => void launch()}
    >
      <PlugIcon aria-hidden /> Connect WhatsApp
    </LoadingButton>
  );
}
