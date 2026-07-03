document.querySelectorAll("[data-fill-login]").forEach((button) => {
  button.addEventListener("click", () => {
    const email = document.querySelector('input[name="email"]');
    const password = document.querySelector('input[name="password"]');
    if (email && password) {
      document.querySelectorAll("[data-fill-login]").forEach((item) => {
        item.classList.remove("is-selected");
      });
      button.classList.add("is-selected");
      email.value = button.dataset.fillLogin;
      password.value = "oilkam123";
      password.focus();
    }
  });
});
