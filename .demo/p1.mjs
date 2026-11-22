import { open, newChat, send, shot } from "./live.mjs";
const { browser, page } = await open();
const box = await newChat(page);
await send(page, box, "Please change the priority of the bar stools task to high.");
await page.waitForTimeout(25000);
await shot(page, "shots/p1-explore.png");
await browser.close();
