document.querySelectorAll("[data-sale-bundle]").forEach((bundle) => {
  const toggle = bundle.querySelector("[data-bundle-toggle]");
  const sales = Array.from(bundle.querySelectorAll('[name="sale_keys"]'));
  bundle.querySelector("[data-bundle-toggle-label]").hidden = false;
  const sync = () => {
    const selected = sales.filter((sale) => sale.checked).length;
    toggle.checked = selected === sales.length;
    toggle.indeterminate = selected > 0 && selected < sales.length;
  };
  toggle.addEventListener("change", () => {
    sales.forEach((sale) => { sale.checked = toggle.checked; });
    sync();
  });
  sales.forEach((sale) => sale.addEventListener("change", sync));
  sync();
});
const eventPicker = document.getElementById("allocation-event");
const eventFields = document.getElementById("allocation-new-event");
const syncEventFields = () => {
  const creating = eventPicker.value === "0";
  eventFields.hidden = !creating;
  eventFields.disabled = !creating;
  eventFields.querySelectorAll("input").forEach((input) => { input.required = creating; });
};
eventPicker.addEventListener("change", syncEventFields);
syncEventFields();
