/**
 * Test helpers for Ant Design controls (#92).
 *
 * An antd `Select` is a custom popup control, not a native `<select>`:
 * `getByLabelText` / `getByRole("combobox")` address the readonly
 * combobox input, which never carries the chosen value, and the option
 * list only exists in the DOM while the popup is open. These helpers
 * keep the three recurring operations readable at call sites:
 * read the shown selection, pick an option, list the option labels.
 *
 * App selects set `virtual={false}` (all lists are small), so every
 * option mounts as a real `[role="option"]` item once the popup opens.
 * Popup open/closed state is read from `aria-expanded` — never toggle
 * blindly, or a click meant to open can close it instead.
 */

import { screen, waitFor } from "@testing-library/react";
import type { UserEvent } from "@testing-library/user-event";

/** The select root element for a combobox returned by getByLabelText. */
export function antdSelectRoot(combobox: HTMLElement): HTMLElement {
  const root = combobox.closest(".ant-select");
  if (!(root instanceof HTMLElement)) {
    throw new Error("not an antd Select combobox");
  }
  return root;
}

/** The label the select currently displays — the `toHaveValue` analog. */
export function antdSelectText(combobox: HTMLElement): string {
  return (
    antdSelectRoot(combobox)
      .querySelector(".ant-select-content")
      ?.textContent?.trim() ?? ""
  );
}

async function openPopup(user: UserEvent, combobox: HTMLElement) {
  if (combobox.getAttribute("aria-expanded") !== "true") {
    await user.click(combobox);
    await waitFor(() =>
      expect(combobox).toHaveAttribute("aria-expanded", "true"),
    );
  }
}

async function closePopup(user: UserEvent, combobox: HTMLElement) {
  if (combobox.getAttribute("aria-expanded") === "true") {
    // Escape does not collapse the popup under jsdom; toggling the
    // combobox does.
    await user.click(combobox);
    await waitFor(() =>
      expect(combobox).not.toHaveAttribute("aria-expanded", "true"),
    );
  }
}

/** `user.selectOptions` analog: open the popup and click the option
 *  whose label equals `label`. */
export async function pickOption(
  user: UserEvent,
  combobox: HTMLElement,
  label: string,
): Promise<void> {
  await openPopup(user, combobox);
  await user.click(await screen.findByRole("option", { name: label }));
  await waitFor(() =>
    expect(combobox).not.toHaveAttribute("aria-expanded", "true"),
  );
}

/** Labels of every option the select offers — opens the popup, reads
 *  the listbox, and closes it again. */
export async function antdOptionLabels(
  user: UserEvent,
  combobox: HTMLElement,
): Promise<string[]> {
  await openPopup(user, combobox);
  const options = await screen.findAllByRole("option");
  const labels = options.map(
    (o) => o.getAttribute("aria-label") ?? o.textContent?.trim() ?? "",
  );
  await closePopup(user, combobox);
  return labels;
}
