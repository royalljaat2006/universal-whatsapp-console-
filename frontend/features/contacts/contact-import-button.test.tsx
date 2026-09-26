import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ContactImportButton } from "./contact-import-button";

const upload = vi.fn();
const get = vi.fn();
vi.mock("@/services/contacts", () => ({
  contactImportService: {
    upload: (...args: unknown[]) => upload(...args),
    get: (...args: unknown[]) => get(...args),
  },
}));

let hasPermission: (code: string) => boolean = () => true;
vi.mock("@/stores/session-context", () => ({
  useSession: () => ({ hasPermission: (code: string) => hasPermission(code) }),
}));

function csvFile(): File {
  return new File(["phone_number\n919876500001\n"], "contacts.csv", { type: "text/csv" });
}

describe("ContactImportButton", () => {
  it("renders nothing without contacts.manage", () => {
    hasPermission = () => false;
    const { container } = render(<ContactImportButton onImported={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
    hasPermission = () => true;
  });

  it("uploads the selected file with the tag name and shows the finished summary", async () => {
    const onImported = vi.fn();
    upload.mockResolvedValueOnce({
      id: "job-1",
      business_id: "b1",
      filename: "contacts.csv",
      tag_name: "vip",
      status: "pending",
      total_rows: null,
      created_count: 0,
      updated_count: 0,
      failed_count: 0,
      row_errors: [],
      error: "",
      started_at: null,
      finished_at: null,
      requested_by_email: null,
      created_at: new Date().toISOString(),
    });
    get.mockResolvedValueOnce({
      id: "job-1",
      business_id: "b1",
      filename: "contacts.csv",
      tag_name: "vip",
      status: "succeeded",
      total_rows: 1,
      created_count: 1,
      updated_count: 0,
      failed_count: 0,
      row_errors: [],
      error: "",
      started_at: new Date().toISOString(),
      finished_at: new Date().toISOString(),
      requested_by_email: null,
      created_at: new Date().toISOString(),
    });

    render(<ContactImportButton onImported={onImported} />);
    fireEvent.click(screen.getByRole("button", { name: "Import CSV" }));

    const file = csvFile();
    fireEvent.change(screen.getByLabelText("CSV file"), { target: { files: [file] } });
    fireEvent.change(screen.getByLabelText("Tag everyone imported (optional)"), {
      target: { value: "vip" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));

    await waitFor(() => expect(upload).toHaveBeenCalledWith(file, "vip"));
    // The component polls 1.5s after receiving the "pending" job.
    await waitFor(() => expect(get).toHaveBeenCalledWith("job-1"), { timeout: 3000 });
    await waitFor(() => expect(onImported).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/1 created, 0 updated/)).toBeInTheDocument();
  });

  it("shows the backend error when the upload itself fails", async () => {
    const { ApiError } = await import("@/lib/api/errors");
    upload.mockRejectedValueOnce(new ApiError(400, "VALIDATION_ERROR", "File too large."));

    render(<ContactImportButton onImported={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Import CSV" }));
    fireEvent.change(screen.getByLabelText("CSV file"), { target: { files: [csvFile()] } });
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("File too large.");
  });
});
