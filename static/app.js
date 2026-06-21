// Favorites via AJAX
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.fav-btn').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.preventDefault();
      const id = btn.dataset.id;
      const res = await fetch(`/favorite/${id}`, { method: 'POST' });
      const data = await res.json();
      if (data.status === 'added') {
        btn.classList.add('active');
        btn.textContent = btn.id === 'watchFavBtn' ? '★ Favoritado' : '★';
      } else {
        btn.classList.remove('active');
        btn.textContent = btn.id === 'watchFavBtn' ? '☆ Favoritar' : '☆';
      }
    });
  });

  // Tag editor toggle
  document.querySelectorAll('.tag-toggle-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const id = btn.dataset.id;
      const editor = document.getElementById(`te-${id}`);
      if (editor) editor.style.display = editor.style.display === 'none' ? 'block' : 'none';
    });
  });
});
