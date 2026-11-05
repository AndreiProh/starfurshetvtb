document.addEventListener("DOMContentLoaded", () => {
  const category = document.getElementById("category");
  const cards = [...document.querySelectorAll(".product-card")];
  const totalEl = document.getElementById("total");
  const summary = document.getElementById("cart-summary");
  const cartCount = document.getElementById("cart-count");
  const headerCart = document.getElementById("header-cart");
  const clearCartButton = document.getElementById("clear-cart");
  const form = document.getElementById("order-form");
  const method = document.getElementById("delivery-method");
  const addressInput = form.querySelector('[name="address"]');

  function updateTotal() {
    let total = 0;
    let itemCount = 0;
    const chosen = [];
    form.querySelectorAll('input[name^="qty_"]').forEach(input => {
      const quantity = Math.max(0, Math.min(99, parseInt(input.value || "0", 10) || 0));
      if (quantity !== Number(input.value)) input.value = quantity;
      if (quantity > 0) {
        const line = Number(input.dataset.price) * quantity;
        total += line;
        itemCount += quantity;
        chosen.push({ name: input.dataset.name, quantity, unit: input.dataset.unit, line });
      }
    });
    totalEl.textContent = total.toFixed(2);

    if (cartCount) {
      cartCount.textContent = String(itemCount);
      cartCount.hidden = itemCount === 0;
      if (headerCart) headerCart.setAttribute("aria-label", `Корзина, ${itemCount} шт., перейти к оформлению`);
    }

    summary.replaceChildren();
    if (!chosen.length) {
      summary.textContent = "Вы пока не добавили товары.";
      summary.classList.add("cart-summary-empty");
    } else {
      summary.classList.remove("cart-summary-empty");
      const list = document.createElement("ul");
      list.className = "cart-items-list";
      chosen.forEach(item => {
        const row = document.createElement("li");
        const name = document.createElement("span");
        name.className = "cart-item-name";
        name.textContent = `${item.name} × ${item.quantity} ${item.unit}`;
        const price = document.createElement("strong");
        price.className = "cart-item-price";
        price.textContent = item.line.toFixed(2);
        row.append(name, price);
        list.append(row);
      });
      summary.append(list);
    }
  }

  form.addEventListener("click", event => {
    const button = event.target.closest(".qty-btn");
    if (!button) return;
    const input = button.parentElement.querySelector("input");
    input.value = Math.max(0, Math.min(99, (parseInt(input.value || "0", 10) || 0) + Number(button.dataset.step)));
    updateTotal();
  });
  form.addEventListener("input", event => {
    if (event.target.matches('input[name^="qty_"]')) updateTotal();
  });
  if (headerCart) {
    headerCart.addEventListener("click", event => {
      const checkout = document.getElementById("checkout");
      if (!checkout) return;
      event.preventDefault();
      checkout.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  if (clearCartButton) {
    clearCartButton.addEventListener("click", () => {
      form.querySelectorAll('input[name^="qty_"]').forEach(input => { input.value = "0"; });
      updateTotal();
    });
  }

  category.addEventListener("change", () => {
    cards.forEach(card => card.hidden = Boolean(category.value && card.dataset.category !== category.value));
  });
  method.addEventListener("change", () => {
    if (method.value === "pickup") {
      addressInput.placeholder = "Укажите адрес или точку самовывоза";
    } else {
      addressInput.placeholder = "Улица, дом, квартира";
    }
  });
  form.addEventListener("submit", event => {
    const hasItems = [...form.querySelectorAll('input[name^="qty_"]')].some(input => Number(input.value) > 0);
    if (!hasItems) {
      event.preventDefault();
      alert("Добавьте хотя бы один товар в заказ.");
    }
  });
  updateTotal();
});
