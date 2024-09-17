/*
This Cloudfront function sits between the browser/viewer requests and Cloudfront routing/behaviors
It updates incoming URI requests to the appropriate Angular/React SPA route
*/

function handler(event) {
    var request = event.request;
    // strip off the initial slash
    var remainingPath = request.uri.substr(1);

    // We always want to point to our "live" app location which is a subdirectory
    // "live/" in the s3 bucket
   var pageLocation = '/live/';

    // We want to see if there is a DEMO label indicating a PR branch
    // if so, then we add that piece after the /live/ portion of the path
    // NOTE: We need DEMO to be the end of the branch name so we have something
    //       to key off of indicating what the base location of our app should be
    var demoIndex = remainingPath.indexOf('DEMO');
    if (demoIndex >= 0) {
        // `feature/imap-123/DEMO/index.html`
        // We don't know whether there is a "/" in the name or not, so just
        // split before it and add it manually
        // i.e. we could get a user requesting DEMO or DEMO/route1
        // but we want the slash in both cases for later referencing
        pageLocation += remainingPath.substr(0, demoIndex + 4) + '/';
        // but we want to strip off that slash from the remainingPath if it is present
        // remainingPath becomes emptry string or route1 in the examples above
        remainingPath = remainingPath.substr(demoIndex + 5);
    }

    // If it is an object (has a period indicating file extension) then we
    // want the path to that object. Otherwise, we got a path route from
    // the app and want to redirect to the SPA index.html and let the browser
    // take care of the routing
    if (remainingPath.includes('.')) {
        // Case for all other project based files included in path
        request.uri = pageLocation + remainingPath;
    } else {
        // Case for everything else, just redirect to index.html
        request.uri = pageLocation + 'index.html';
    }
    return request
}
