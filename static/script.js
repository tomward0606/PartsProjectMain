function addToBasket(partNumber) {
  fetch(`/add_to_basket/${encodeURIComponent(partNumber)}`, {
    method: 'GET',
    headers: { 'X-Requested-With': 'XMLHttpRequest' }
  }).then(response => {
    if (response.status === 204) {
      showToast();
    }
  });
}

function showToast() {
  const toast = document.getElementById('toast');
  toast.style.display = 'block';
  setTimeout(hideToast, 2500);
}

function hideToast() {
  const toast = document.getElementById('toast');
  toast.style.display = 'none';
}
