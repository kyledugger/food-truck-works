# Food Truck Works teaser page

Copy templates/coming_soon.html and the static files in this package into the matching folders in your current app. No database migration or new Python dependency is required.

In main.py, add this context to the existing root route's TemplateResponse:

```python
return templates.TemplateResponse(
    request=request,
    name="coming_soon.html",
    context={"launch_updates_url": os.getenv("LAUNCH_UPDATES_URL", "")},
)
```

Keep the existing signed-in redirect to /dashboard above it. No registration or login rules change.

Optional: set LAUNCH_UPDATES_URL to your hosted newsletter/waitlist signup form's HTTPS URL. When configured, the page displays Get launch updates buttons linking to that form. Without it, buttons navigate to the feature section. This package does not collect email addresses or send marketing emails.

The page keeps V1 scope open and explains that some features will follow later. Cards are concept illustrations rather than screenshots. Payroll services are intentionally not named until supported connections are confirmed.

The founder text is drafted in first person. Review it before deploying. Two optimized photos from your uploads are included; originals are unchanged.

Deploy these files using your normal git/Render workflow. Test / while logged out and check mobile layout. If old content appears, refresh and purge cached HTML for the homepage as needed. The teaser stylesheet uses a versioned URL.
