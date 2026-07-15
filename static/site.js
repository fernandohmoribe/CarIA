const FAVORITOS_KEY = "cariar_favoritos";

function obterFavoritos() {
    try {
        const dados = JSON.parse(localStorage.getItem(FAVORITOS_KEY) || "[]");
        return Array.isArray(dados) ? dados : [];
    } catch (e) {
        return [];
    }
}

function salvarFavoritos(slugs) {
    localStorage.setItem(FAVORITOS_KEY, JSON.stringify(slugs));
}

function ehFavorito(slug) {
    return obterFavoritos().includes(slug);
}

function _aplicarEstadoBotao(btnEl, favorito) {
    btnEl.classList.toggle("is-favorito", favorito);
    btnEl.setAttribute("aria-pressed", String(favorito));
}

function alternarFavorito(slug, btnEl) {
    const favoritos = obterFavoritos();
    const indice = favoritos.indexOf(slug);
    const favoritoAgora = indice === -1;
    if (favoritoAgora) {
        favoritos.push(slug);
    } else {
        favoritos.splice(indice, 1);
    }
    salvarFavoritos(favoritos);
    if (btnEl) {
        _aplicarEstadoBotao(btnEl, favoritoAgora);
    }
    return favoritoAgora;
}

function aplicarEstadoFavoritosNaPagina() {
    const favoritos = obterFavoritos();
    document.querySelectorAll("[data-favorito-slug]").forEach(function (btnEl) {
        _aplicarEstadoBotao(btnEl, favoritos.includes(btnEl.dataset.favoritoSlug));
    });
}

function maskPreco(el) {
    let digits = el.value.replace(/\D/g, "").replace(/^0+(?=\d)/, "");
    if (!digits) { el.value = ""; return; }
    while (digits.length < 3) digits = "0" + digits;
    const cents = digits.slice(-2);
    const intPart = digits.slice(0, -2).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
    el.value = intPart + "," + cents;
}

document.addEventListener("DOMContentLoaded", aplicarEstadoFavoritosNaPagina);
