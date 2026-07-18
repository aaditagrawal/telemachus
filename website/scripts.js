// Telemachus website behavior.

(function applySavedTheme() {
    const savedTheme = localStorage.getItem('theme');
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    if (savedTheme === 'dark' || (!savedTheme && prefersDark)) {
        document.documentElement.setAttribute('data-theme', 'dark');
    }
})();

document.addEventListener('DOMContentLoaded', function() {
    const themeToggle = document.getElementById('theme-toggle');
    themeToggle?.addEventListener('click', () => {
        const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
        if (isDark) {
            document.documentElement.removeAttribute('data-theme');
        } else {
            document.documentElement.setAttribute('data-theme', 'dark');
        }
        localStorage.setItem('theme', isDark ? 'light' : 'dark');
    });

    const mobileMenuButton = document.getElementById('mobile-menu-btn');
    const navigation = document.querySelector('.nav-links');
    mobileMenuButton?.addEventListener('click', () => {
        navigation?.classList.toggle('active');
        mobileMenuButton.setAttribute(
            'aria-expanded',
            navigation?.classList.contains('active') ? 'true' : 'false'
        );
    });

    document.querySelectorAll('.nav-link').forEach(link => {
        link.addEventListener('click', () => {
            navigation?.classList.remove('active');
            mobileMenuButton?.setAttribute('aria-expanded', 'false');
        });
    });

    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function(event) {
            const target = document.querySelector(this.getAttribute('href'));
            if (!target) return;
            event.preventDefault();
            const headerHeight = document.querySelector('header')?.offsetHeight ?? 0;
            window.scrollTo({
                top: target.offsetTop - headerHeight - 20,
                behavior: 'smooth'
            });
        });
    });

    const observer = 'IntersectionObserver' in window
        ? new IntersectionObserver(entries => {
            entries.forEach(entry => {
                if (!entry.isIntersecting) return;
                entry.target.classList.add('animate-in');
                observer.unobserve(entry.target);
            });
        }, { threshold: 0.1, rootMargin: '0px 0px -50px 0px' })
        : null;

    document.querySelectorAll(
        '.special-feature-item, .step, .download-card, .faq-item'
    ).forEach((element, index) => {
        element.style.transitionDelay = `${index * 0.05}s`;
        if (observer) {
            observer.observe(element);
        } else {
            element.classList.add('animate-in');
        }
    });

    const header = document.getElementById('header');
    window.addEventListener('scroll', () => {
        header?.classList.toggle('scrolled', window.scrollY > 50);
    }, { passive: true });

    document.querySelectorAll('.faq-item summary').forEach(summary => {
        summary.addEventListener('click', function() {
            document.querySelectorAll('.faq-item[open]').forEach(item => {
                if (item !== this.parentElement) item.removeAttribute('open');
            });
        });
    });

    const repositoryURL = document
        .querySelector('meta[name="repository-url"]')
        ?.getAttribute('content')
        ?.trim()
        ?.replace(/\/+$/, '');

    if (repositoryURL) {
        document.querySelectorAll('[data-repository-link]').forEach(link => {
            link.href = repositoryURL + (link.dataset.repositoryPath || '');
            link.hidden = false;
        });
    }
});
