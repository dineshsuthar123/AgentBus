import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { VirtualList } from "./VirtualList";

describe("VirtualList", () => {
  it("navigates fifty thousand logical records without mounting them all", () => {
    render(<VirtualList ariaLabel="Large logs" height={240} itemCount={50_000} rowHeight={20} renderItem={(index) => <span>Log {index}</span>} />);
    const list = screen.getByRole("list", { name: "Large logs" });

    expect(list.querySelectorAll('[role="listitem"]').length).toBeLessThan(40);
    fireEvent.scroll(list, { target: { scrollTop: 49_000 * 20 } });
    expect(screen.getByText("Log 48992")).toBeInTheDocument();
    expect(list.querySelectorAll('[role="listitem"]').length).toBeLessThan(40);
  });
});
