import fs from "node:fs";
import path from "node:path";
import { network } from "hardhat";

const { ethers } = await network.create();

async function main() {
  const [admin] = await ethers.getSigners();
  console.log("Deploying contracts with admin:", admin.address);

  const ecoToken = await ethers.deployContract("EcoToken", [admin.address]);
  await ecoToken.waitForDeployment();

  const ecoCert = await ethers.deployContract("EcoCertificate", [admin.address]);
  await ecoCert.waitForDeployment();

  const auditSystem = await ethers.deployContract("EnergyAuditSystem", [
    admin.address,
    await ecoToken.getAddress(),
    await ecoCert.getAddress(),
  ]);
  await auditSystem.waitForDeployment();

  const MINTER_ROLE = await ecoToken.MINTER_ROLE();
  await (await ecoToken.grantRole(MINTER_ROLE, await auditSystem.getAddress())).wait();
  await (await ecoCert.grantRole(MINTER_ROLE, await auditSystem.getAddress())).wait();

  const deployment = {
    network: "localhost",
    admin: admin.address,
    ecoToken: await ecoToken.getAddress(),
    ecoCertificate: await ecoCert.getAddress(),
    energyAuditSystem: await auditSystem.getAddress(),
  };

  const outPath = path.resolve(process.cwd(), "deployment.json");
  fs.writeFileSync(outPath, JSON.stringify(deployment, null, 2));

  console.log("Deployment saved to:", outPath);
  console.log(deployment);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});