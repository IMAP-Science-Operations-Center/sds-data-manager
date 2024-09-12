import { handler } from './update_uri_routes.js';

// set up a basic event we can manipulate for CloudFront
const event = {
    "version": "1.0",
    "context": {
        "eventType": "viewer-request"
    },
    "viewer": {
        "ip": "198.51.1.1"
    },
    "request": {
        "method": "GET",
        "uri": "/example.png",
        "headers": {
            "host": {"value": "example.org"}
        }
    }
}

const testUris = [
    // basic page
    ["/", "/live/index.html"],
    // routes within the site
    ["/about", "/live/index.html"],
    ["/about?123", "/live/index.html"],
    ["/about/about2", "/live/index.html"],
    // assets within the site
    ["/image.png", "/live/image.png"],
    ["/assets/image.png", "/live/assets/image.png"],
    // PR routes
    ["/feature/IMAP-123/DEMO", "/live/feature/IMAP-123/DEMO/index.html"],
    ["/feature/IMAP-123/DEMO/", "/live/feature/IMAP-123/DEMO/index.html"],
    ["/feature/IMAP-123/DEMO/about", "/live/feature/IMAP-123/DEMO/index.html"],
    ["/feature/IMAP-123/DEMO/about/about2", "/live/feature/IMAP-123/DEMO/index.html"],
    ["/feature/IMAP-123/DEMO/about?123", "/live/feature/IMAP-123/DEMO/index.html"],
    // PR assets
    ["/feature/IMAP-123/DEMO/image.png", "/live/feature/IMAP-123/DEMO/image.png"],
    ["/feature/IMAP-123/DEMO/assets/image.png", "/live/feature/IMAP-123/DEMO/assets/image.png"],
]

testUris.map(testUri => {
    event.request.uri = testUri[0]
    const value = handler(event).uri
    if (value !== testUri[1]) {
        console.error("[Input, Output, Expected]", [testUri[0], value, testUri[1]]);
        process.exit(1);
    }
})
