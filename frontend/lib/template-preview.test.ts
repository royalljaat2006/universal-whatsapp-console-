import { describe, expect, it } from "vitest";

import type { TemplateParameterSpec } from "@/types/api";

import {
  missingParameters,
  parameterField,
  renderPreview,
  serverPath,
  toParameters,
} from "./template-preview";

const specs: TemplateParameterSpec[] = [
  { component: "header", key: "media", kind: "image", example: "", button_index: null },
  { component: "body", key: "1", kind: "text", example: "Asha", button_index: null },
  { component: "body", key: "2", kind: "text", example: "ORD-1", button_index: null },
  { component: "button", key: "0", kind: "text", example: "x", button_index: 0 },
];

describe("template preview helpers", () => {
  it("renders supplied values and leaves missing placeholders visible", () => {
    expect(renderPreview("Hi {{1}}, order {{2}}", { "1": "Asha" })).toBe("Hi Asha, order {{2}}");
    expect(renderPreview("Hi {{ first_name }}", { first_name: "Ravi" })).toBe("Hi Ravi");
  });

  it("builds the backend parameter shape (header asset, body keys, button index)", () => {
    const values = {
      [parameterField(specs[0])]: "asset-1",
      [parameterField(specs[1])]: "Asha",
      [parameterField(specs[2])]: "ORD-1",
      [parameterField(specs[3])]: "track/1",
    };
    expect(toParameters(specs, values)).toEqual({
      header: { media_asset_id: "asset-1" },
      body: { "1": "Asha", "2": "ORD-1" },
      buttons: { "0": "track/1" },
    });
    expect(missingParameters(specs, values)).toEqual([]);
  });

  it("reports blank fields as missing and maps specs to backend error paths", () => {
    expect(missingParameters(specs, { [parameterField(specs[1])]: "  " })).toHaveLength(4);
    expect(specs.map(serverPath)).toEqual(["header.media_asset_id", "body.1", "body.2", "buttons.0"]);
  });
});
