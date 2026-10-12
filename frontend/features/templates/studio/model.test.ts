import { describe, expect, it } from "vitest";

import { emptyContent, fieldIssues, newButton, nextVariableNumber, pruneExamples, variableKeys } from "./model";

describe("variableKeys / nextVariableNumber", () => {
  it("finds variables in order, once each", () => {
    expect(variableKeys("Hi {{1}}, order {{2}} and again {{1}}")).toEqual(["1", "2"]);
    expect(variableKeys("Hi {{ name }}")).toEqual(["name"]);
    expect(variableKeys("no variables")).toEqual([]);
  });
  it("the next number never leaves a gap and ignores named variables", () => {
    expect(nextVariableNumber("")).toBe(1);
    expect(nextVariableNumber("Hi {{1}} {{2}}")).toBe(3);
    expect(nextVariableNumber("Hi {{first_name}}")).toBe(1);
  });
});

describe("pruneExamples", () => {
  it("keeps only examples for variables that still exist in the header or body", () => {
    const content = {
      ...emptyContent(),
      header_text: "Order {{1}}",
      body: "Hi {{1}} and {{2}} ok",
      examples: { "1": "A", "2": "B", "3": "stale" },
    };
    expect(pruneExamples(content).examples).toEqual({ "1": "A", "2": "B" });
  });
});

describe("buttons and issues", () => {
  it("each button type starts with only the fields it needs", () => {
    expect(newButton("QUICK_REPLY")).toEqual({ type: "QUICK_REPLY", text: "" });
    expect(newButton("URL")).toMatchObject({ type: "URL", url: "https://" });
    expect(newButton("PHONE_NUMBER")).toMatchObject({ type: "PHONE_NUMBER", phone_number: "" });
  });
  it("fieldIssues matches a field and its children", () => {
    const issues = [{ field: "body" }, { field: "body.1" }, { field: "bodyx" }, { field: "header" }];
    expect(fieldIssues(issues, "body")).toEqual([{ field: "body" }, { field: "body.1" }]);
  });
});
