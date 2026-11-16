(function () {
    var toggle = document.querySelector(".nav-toggle");
    var nav = document.getElementById("main-nav");
    if (toggle && nav) {
        toggle.addEventListener("click", function () {
            var open = nav.classList.toggle("open");
            toggle.setAttribute("aria-expanded", open ? "true" : "false");
        });
    }
    document.addEventListener("click", function (e) {
        var close = e.target.closest(".notice-close");
        if (close) close.parentElement.remove();
    });
})();
