document.addEventListener('DOMContentLoaded', () => {
  //Fade page in and reveal on scroll
  document.body.classList.add('fade-in-on-load')
  const observer = new IntersectionObserver(
    (entries, obs) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          entry.target.classList.add('active')
          obs.unobserve(entry.target)
        }
      })
    },
    { threshold: 0.1 }
  )

  document.querySelectorAll('.reveal').forEach(el => {
    observer.observe(el)
  })
})
