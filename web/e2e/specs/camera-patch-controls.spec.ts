import { expect, test } from "../fixtures/frigate-test";
import { installWsFrameCapture, waitForWsFrame } from "../helpers/ws-frames";

const CAMERA = "front_door";

test.beforeEach(async ({ frigateApp }) => {
  const { page } = frigateApp;
  await frigateApp.api.install({
    config: {
      cameras: {
        [CAMERA]: {
          onvif: { host: "camera.example" },
          patches: [{ id: "fixture" }],
        },
      },
    },
  });
  await page.route(`**/api/${CAMERA}/patches`, (route) =>
    route.fulfill({
      json: {
        patches: [
          {
            id: "fixture",
            available: true,
            controls: [
              {
                id: "siren_off",
                label: "Stop siren",
                kind: "button",
                options: [],
                requires_admin: true,
                readback: "ack",
              },
            ],
            state: {},
            error: "state_failed",
          },
        ],
      },
    }),
  );
  await page.route(`**/api/${CAMERA}/ptz/info`, (route) =>
    route.fulfill({
      json: {
        name: CAMERA,
        features: ["pt"],
        presets: ["Home"],
        profiles: [],
      },
    }),
  );
  await installWsFrameCapture(page);
  await frigateApp.goto(`/#${CAMERA}`);
});

test("mobile patch panels start closed and open one at a time below the player", async ({
  frigateApp,
}) => {
  test.skip(!frigateApp.isMobile, "Mobile view");
  const { page } = frigateApp;
  const controls = page.getByRole("button", {
    name: "Controls",
    exact: true,
  });
  const move = page.getByRole("button", { name: "Move", exact: true });
  const presets = page.getByRole("button", { name: "Presets" });
  const stop = page.getByRole("button", { name: "Stop siren" });

  await expect(controls).toHaveAttribute("aria-expanded", "false");
  await expect(move).toHaveAttribute("aria-expanded", "false");
  await expect(stop).toHaveCount(0);
  await expect(presets).toHaveCount(0);

  await controls.tap();
  await expect(stop).toBeEnabled();
  await expect(move).toHaveAttribute("aria-expanded", "false");
  const playerBox = await page.locator("#player-container").boundingBox();
  const controlsBox = await controls.boundingBox();
  expect(playerBox!.y + playerBox!.height).toBeLessThanOrEqual(
    controlsBox!.y + 1,
  );

  await move.tap();
  await expect(controls).toHaveAttribute("aria-expanded", "false");
  await expect(stop).toHaveCount(0);
  await expect(presets).toBeVisible();
  await move.tap();
  await expect(presets).toHaveCount(0);

  await move.tap();
  await page.keyboard.press("Escape");
  await expect(move).toHaveAttribute("aria-expanded", "false");
  await expect(presets).toHaveCount(0);
  await expect(page).toHaveURL(new RegExp(`#${CAMERA}$`));
});

test("desktop patch controls and native keyboard PTZ are available immediately", async ({
  frigateApp,
}) => {
  test.skip(frigateApp.isMobile, "Desktop view");
  const { page } = frigateApp;
  await expect(
    page.getByRole("region", { name: "Camera controls" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Stop siren" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "Presets" })).toBeVisible();
  await page.keyboard.press("ArrowUp");
  await waitForWsFrame(
    page,
    (frame) => frame.includes(`"${CAMERA}/ptz"`) && frame.includes("MOVE_UP"),
  );
});

test("unpatched cameras do not show the patch controls", async ({
  frigateApp,
}) => {
  await frigateApp.goto("/#garage");
  const { page } = frigateApp;
  await expect(page.locator("#player-container")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Controls", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("region", { name: "Camera controls" }),
  ).toHaveCount(0);
});
