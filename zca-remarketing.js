/**
 * zca-remarketing.js
 * ──────────────────────────────────────────────
 * Tích hợp zca-js với Zalo Scraper Dashboard
 * để remarketing tự động cho các khách hàng đã được duyệt.
 *
 * Cách dùng:
 *   1. Copy file này vào project zca-js của bạn
 *   2. Chạy: node zca-remarketing.js
 * ──────────────────────────────────────────────
 */

import { Zalo, ThreadType } from "zca-js";
import fetch from "node-fetch";

const DASHBOARD_URL = "http://localhost:5000"; // Flask server của scraper
const DELAY_MS      = 3000;                    // Delay giữa các tin (tránh spam)

// ── Remarketing message template ──────────────
// {name} sẽ được thay bằng tên thực của khách
const MESSAGE_TEMPLATE = `Chào {name}! 👋
Cảm ơn bạn đã quan tâm đến sản phẩm của chúng mình.
Bạn có muốn chúng mình tư vấn thêm không ạ? 😊`;

// ──────────────────────────────────────────────
// STEP 1: Import friend list → match với DB
// ──────────────────────────────────────────────
async function syncFriendsToDashboard(zca) {
  console.log("📋 Đang lấy danh sách bạn bè từ Zalo...");
  
  const friendResult = await zca.getFriendList({ page: 0, count: 200 });
  const friends = friendResult?.data?.friends || friendResult?.data || [];
  
  console.log(`👥 Tìm thấy ${friends.length} người trong danh bạ`);

  const res = await fetch(`${DASHBOARD_URL}/api/zca/import-friends`, {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ friends }),
  });

  const result = await res.json();
  
  if (result.ok) {
    console.log(`✅ Match thành công: ${result.matched}/${result.total_friends} bạn bè`);
    if (result.unmatchedNames?.length) {
      console.log("⚠️  Chưa match được:", result.unmatchedNames.join(", "));
    }
  } else {
    console.error("❌ Lỗi import friends:", result.error);
  }

  return result;
}

// ──────────────────────────────────────────────
// STEP 2: Lấy danh sách cần remarketing & gửi tin
// ──────────────────────────────────────────────
async function sendRemarketing(zca, customTemplate = null) {
  console.log("\n🚀 Đang lấy danh sách remarketing...");
  
  const res  = await fetch(`${DASHBOARD_URL}/api/zca/remarketing-list`);
  const data = await res.json();
  
  if (!data.ok || !data.contacts.length) {
    console.log("⚠️  Không có contact nào. Cần: status=approved + zalo_uid hợp lệ.");
    return;
  }

  const template = customTemplate || data.template || MESSAGE_TEMPLATE;
  console.log(`📣 Sẽ gửi tin đến ${data.total} contact...`);

  let sent = 0;
  let failed = 0;

  for (const contact of data.contacts) {
    const msg = template
      .replace(/\{name\}/g, contact.name)
      .replace(/\{userId\}/g, contact.userId);

    try {
      await zca.sendMessage(
        { msg },
        Number(contact.userId),
        ThreadType.User
      );
      console.log(`  ✅ Đã gửi → ${contact.name} (${contact.userId})`);
      sent++;
    } catch (err) {
      console.error(`  ❌ Lỗi gửi → ${contact.name}: ${err.message}`);
      failed++;
    }

    // Delay giữa các tin (tránh bị khóa tài khoản)
    if (data.contacts.indexOf(contact) < data.contacts.length - 1) {
      await sleep(DELAY_MS);
    }
  }

  console.log(`\n📊 Kết quả: ${sent} thành công, ${failed} thất bại`);
}

// ──────────────────────────────────────────────
// STEP 3: Kiểm tra trạng thái
// ──────────────────────────────────────────────
async function checkStats() {
  const res  = await fetch(`${DASHBOARD_URL}/api/zca/stats`);
  const data = await res.json();
  console.log("\n📊 Trạng thái ZCA:");
  console.log(`  Tổng contacts: ${data.total}`);
  console.log(`  Đã match UID:  ${data.matched}`);
  console.log(`  Đã duyệt:      ${data.approved}`);
  console.log(`  Sẵn sàng gửi: ${data.readyToSend}`);
  return data;
}

// ──────────────────────────────────────────────
// UTILS
// ──────────────────────────────────────────────
function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// ──────────────────────────────────────────────
// MAIN
// ──────────────────────────────────────────────
async function main() {
  console.log("╔══════════════════════════════════════════╗");
  console.log("║  ZCA Remarketing — Tích hợp với Scraper  ║");
  console.log("╚══════════════════════════════════════════╝\n");

  const zca = new Zalo(
    {
      cookie:             {},        // 👉 thay bằng cookie Zalo của bạn
      imei:               "",        // 👉 imei
      userAgent:          "",        // 👉 user agent
    },
    { selfListen: false }
  );

  await zca.login();
  console.log("✅ Đã đăng nhập Zalo\n");

  // ── Chạy theo thứ tự ──────────────────────
  // 1. Đồng bộ friend list vào dashboard
  await syncFriendsToDashboard(zca);

  // 2. Kiểm tra trạng thái
  await checkStats();

  // 3. Gửi tin remarketing (uncomment khi sẵn sàng)
  // await sendRemarketing(zca);
  
  // Hoặc dùng template tùy chỉnh:
  // await sendRemarketing(zca, "Chào {name}! Bên mình có chương trình ưu đãi mới 🎉");
}

main().catch(console.error);
