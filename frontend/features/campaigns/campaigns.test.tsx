import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LineChart } from "@/components/charts/line-chart";
import type { CampaignProgress, RecipientStatus } from "@/types/api";

import { CampaignProgressBar, CONTROLS } from "./campaign-progress";

function progress(byStatus: Partial<Record<RecipientStatus, number>>): CampaignProgress {
  const all: Record<RecipientStatus, number> = {
    pending: 0,
    queued: 0,
    sending: 0,
    accepted: 0,
    sent: 0,
    delivered: 0,
    read: 0,
    failed: 0,
    skipped: 0,
    cancelled: 0,
    ...byStatus,
  };
  const total = Object.values(all).reduce((a, b) => a + b, 0);
  const notDone = all.pending + all.queued + all.sending;
  return {
    total,
    by_status: all,
    processed: total - notDone,
    in_flight: all.queued + all.sending,
    accepted_or_later: all.accepted + all.sent + all.delivered + all.read,
    sent_or_later: all.sent + all.delivered + all.read,
    delivered_or_later: all.delivered + all.read,
    read: all.read,
    failed: all.failed,
    skipped: all.skipped,
    cancelled: all.cancelled,
    pending: all.pending,
  };
}

describe("CampaignProgressBar", () => {
  it("shows exactly the persisted processed/total counts", () => {
    render(
      <CampaignProgressBar progress={progress({ accepted: 400, delivered: 30, failed: 7, pending: 63 })} />,
    );
    expect(screen.getByLabelText("437 of 500 recipients processed")).toBeInTheDocument();
    expect(screen.getByText(/437 \/ 500 processed/)).toBeInTheDocument();
    expect(screen.getByText(/63 pending/)).toBeInTheDocument();
  });

  it("does not invent progress before the audience snapshot exists", () => {
    render(<CampaignProgressBar progress={progress({})} />);
    expect(screen.getByText(/No recipients yet/)).toBeInTheDocument();
  });
});

describe("campaign controls", () => {
  it("mirror the backend state rules", () => {
    expect(CONTROLS.draft).toEqual([]);
    expect(CONTROLS.running).toContain("pause");
    expect(CONTROLS.paused).toContain("resume");
    expect(CONTROLS.completed).toEqual(["retry"]);
    expect(CONTROLS.cancelled).toEqual([]);
  });
});

describe("LineChart", () => {
  it("exposes the plotted values as an accessible table", () => {
    render(
      <LineChart
        ariaLabel="Volume"
        labels={["09-20", "09-21"]}
        series={[{ name: "Outbound", color: "#000", values: [5, 0] }]}
      />,
    );
    expect(screen.getByRole("img", { name: "Volume" })).toBeInTheDocument();
    const cells = screen.getAllByRole("cell").map((c) => c.textContent);
    expect(cells).toEqual(["09-20", "5", "09-21", "0"]);
  });
});
