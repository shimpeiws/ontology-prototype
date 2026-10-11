/**
 * Ant Design × jsdom compatibility probe (#92).
 *
 * Verifies that the antd controls used across the app keep working under
 * the Vitest jsdom environment — roles, label association, and the
 * Select popup mechanics the `antdSelect` test helpers rely on.
 */

import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Alert, Button, Checkbox, Input, Select } from "antd";

import { antdOptionLabels, antdSelectText, pickOption } from "./antdSelect";

describe("antd jsdom probe", () => {
  it("Select opens, picks, and displays an option", async () => {
    const user = userEvent.setup();
    function Harness() {
      const [v, setV] = useState("");
      return (
        <>
          <label htmlFor="fmt">Format</label>
          <Select
            virtual={false}
            id="fmt"
            value={v}
            options={[
              { value: "", label: "(unset)" },
              { value: "csv", label: "csv" },
              { value: "json", label: "json" },
            ]}
            onChange={setV}
          />
        </>
      );
    }
    render(<Harness />);
    const combo = screen.getByLabelText("Format");
    expect(combo).toHaveAttribute("role", "combobox");
    expect(await antdOptionLabels(user, combo)).toEqual([
      "(unset)",
      "csv",
      "json",
    ]);
    expect(antdSelectText(combo)).toBe("(unset)");
    await pickOption(user, combo, "json");
    expect(antdSelectText(combo)).toBe("json");
  });

  it("Checkbox/Input/Button keep roles and label association", async () => {
    const user = userEvent.setup();
    const onCheck = vi.fn();
    const onClick = vi.fn();
    render(
      <>
        <Checkbox id="c" onChange={(e) => onCheck(e.target.checked)}>
          flag
        </Checkbox>
        <label htmlFor="n">name</label>
        <Input id="n" defaultValue="x" />
        <Button onClick={onClick}>go</Button>
      </>,
    );
    await user.click(screen.getByRole("checkbox", { name: "flag" }));
    expect(onCheck).toHaveBeenCalledWith(true);
    expect(screen.getByLabelText("name")).toHaveValue("x");
    await user.click(screen.getByRole("button", { name: "go" }));
    expect(onClick).toHaveBeenCalled();
    render(<Alert type="info" title="hi" />);
    expect(screen.getByText("hi")).toBeInTheDocument();
  });
});
