import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import App from "./main";

describe("MateScope shell", () => {
  it("renders the bilingual shell and status control", () => {
    render(<App />);
    expect(screen.getAllByText("MateScope")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "中文" })).toBeInTheDocument();
    expect(screen.getByText("The application shell is ready.")).toBeInTheDocument();
  });
});
